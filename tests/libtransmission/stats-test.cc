// This file may be used under GPLv2 or GPLv3, as described in the licenses/ folder.

#include <chrono>
#include <filesystem>

#include "libtransmission/api-compat.h"
#include "libtransmission/stats.h"
#include "test-fixtures.h"

using StatsTest = tr::test::SandboxedTest;

TEST_F(StatsTest, MissingStatsFileStartsWithEmptyHistory)
{
    auto const now = time(nullptr);
    auto const path = sandboxDir() + "/stats.json";
    ASSERT_FALSE(std::filesystem::exists(path));

    auto stats = tr_stats{ sandboxDir(), now };
    auto const history = stats.history(now);
    auto const& map = *history.get_if<tr_variant::Map>();

    EXPECT_EQ(now, map.value_if<int64_t>(tr_quark_new("started_at")));
    EXPECT_TRUE(map.find_if<tr_variant::Vector>(tr_quark_new("days"))->empty());
    EXPECT_TRUE(map.find_if<tr_variant::Vector>(tr_quark_new("hours"))->empty());
}

TEST_F(StatsTest, CheckpointsEveryThirtyMinutesAndSavesOnShutdown)
{
    auto const checkpoint = tr_stats::Clock::now();
    auto const path = sandboxDir() + "/stats.json";
    auto read_downloaded = [&]() -> int64_t
    {
        auto value = tr_variant_serde::json().parse_file(path);
        if (!value)
        {
            return -1;
        }
        tr::api_compat::convert_incoming_data(*value);
        return value->get_if<tr_variant::Map>()->value_if<int64_t>(TR_KEY_downloaded_bytes).value_or(-1);
    };

    {
        auto stats = tr_stats{ sandboxDir(), time(nullptr), checkpoint };
        for (auto minute = 6; minute <= 60; minute += 6)
        {
            stats.add_downloaded(100);
            stats.save_if_dirty(checkpoint + std::chrono::minutes{ minute });
            auto const expected = minute < 30 ? -1 : (minute < 60 ? 500 : 1000);
            EXPECT_EQ(expected, read_downloaded());
        }
        stats.add_downloaded(77);
        EXPECT_EQ(1000, read_downloaded());
    }
    EXPECT_EQ(1077, read_downloaded());
    std::cout << "SSD_STATS: 10 periodic opportunities, 2 checkpoints, final counters preserved\n";
}

TEST_F(StatsTest, FailedCheckpointRemainsDirtyAndRetries)
{
    auto const directory = sandboxDir() + "/missing";
    auto const checkpoint = tr_stats::Clock::now();
    auto stats = tr_stats{ directory, time(nullptr), checkpoint };
    stats.add_uploaded(123);
    stats.save_if_dirty(checkpoint + 30min);
    EXPECT_FALSE(std::filesystem::exists(directory + "/stats.json"));
    ASSERT_TRUE(std::filesystem::create_directory(directory));
    stats.save_if_dirty(checkpoint + 36min);
    EXPECT_TRUE(std::filesystem::exists(directory + "/stats.json"));
    stats.clear();
    ASSERT_TRUE(stats.save());
    EXPECT_EQ(0U, stats.cumulative().uploadedBytes);
}

TEST_F(StatsTest, HistoryKeepsUtcBoundariesAndSurvivesRestart)
{
    auto const now = time(nullptr);
    auto const midnight = now / 86400 * 86400;
    {
        auto stats = tr_stats{ sandboxDir(), midnight - 3600 };
        stats.add_downloaded(111, midnight - 1);
        stats.add_uploaded(222, midnight);
        auto value = stats.history(now);
        auto const& map = *value.get_if<tr_variant::Map>();
        auto const* days = map.find_if<tr_variant::Vector>(tr_quark_new("days"));
        ASSERT_NE(nullptr, days);
        ASSERT_EQ(2U, days->size());
        uint64_t down = 0, up = 0;
        for (auto const& item : *days)
        {
            auto const& row = *item.get_if<tr_variant::Vector>();
            EXPECT_EQ(0, row[0].value_if<int64_t>().value() % 86400);
            down += row[1].value_if<uint64_t>().value();
            up += row[2].value_if<uint64_t>().value();
        }
        EXPECT_EQ(111U, down);
        EXPECT_EQ(222U, up);
        ASSERT_TRUE(stats.save());
    }
    auto loaded = tr_stats{ sandboxDir(), now };
    auto history = loaded.history(now);
    auto const& map = *history.get_if<tr_variant::Map>();
    EXPECT_EQ(midnight - 3600, map.value_if<int64_t>(tr_quark_new("started_at")));
    EXPECT_EQ(2U, map.find_if<tr_variant::Vector>(tr_quark_new("days"))->size());
    EXPECT_EQ(2U, map.find_if<tr_variant::Vector>(tr_quark_new("hours"))->size());
    EXPECT_EQ(111U, loaded.cumulative().downloadedBytes);
    EXPECT_EQ(222U, loaded.cumulative().uploadedBytes);
}

TEST_F(StatsTest, HistoryIsBoundedAndClearResetsIt)
{
    auto const now = time(nullptr);
    auto stats = tr_stats{ sandboxDir(), now - 100 * 86400 };
    for (int day = 100; day >= 0; --day)
        stats.add_downloaded(1, now - day * 86400);
    auto value = stats.history(now);
    auto const& map = *value.get_if<tr_variant::Map>();
    EXPECT_EQ(62U, map.find_if<tr_variant::Vector>(tr_quark_new("days"))->size());
    EXPECT_LE(map.find_if<tr_variant::Vector>(tr_quark_new("hours"))->size(), 48U);
    auto future = stats.history(now + 63 * 86400);
    EXPECT_TRUE(future.get_if<tr_variant::Map>()->find_if<tr_variant::Vector>(tr_quark_new("days"))->empty());
    EXPECT_EQ(101U, stats.cumulative().downloadedBytes);
    stats.clear();
    value = stats.history(now);
    EXPECT_TRUE(value.get_if<tr_variant::Map>()->find_if<tr_variant::Vector>(tr_quark_new("days"))->empty());
}

TEST_F(StatsTest, BackwardClockDoesNotEvictNewerSlot)
{
    auto const now = time(nullptr);
    auto stats = tr_stats{ sandboxDir(), now };
    stats.add_downloaded(50, now);
    stats.add_downloaded(25, now - 62 * 86400);
    auto value = stats.history(now);
    auto const& days = *value.get_if<tr_variant::Map>()->find_if<tr_variant::Vector>(tr_quark_new("days"));
    ASSERT_EQ(1U, days.size());
    EXPECT_EQ(50, (*days[0].get_if<tr_variant::Vector>())[1].value_if<int64_t>());
    EXPECT_EQ(75U, stats.cumulative().downloadedBytes);
}
