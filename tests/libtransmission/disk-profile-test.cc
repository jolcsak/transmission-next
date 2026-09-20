// SPDX-License-Identifier: GPL-2.0-or-later
#include "libtransmission/disk-profile.h"
#include "libtransmission/dashboard-load.h"
#include "libtransmission/rpcimpl.h"
#include <future>
#include "test-fixtures.h"
#include <filesystem>
#include <fstream>
#ifdef __linux__
#include <sys/stat.h>
#include <sys/sysmacros.h>
#endif
using DiskProfileTest = tr::test::SandboxedTest;

TEST_F(DiskProfileTest, DashboardPressureParserAndUnknownData)
{
    EXPECT_EQ(12.5, tr_dashboard_pressure("some avg10=12.50 avg60=1.0\nfull avg10=2.00\n", "some"));
    EXPECT_EQ(2.0, tr_dashboard_pressure("some avg10=12.50\nfull avg10=2.00\n", "full"));
    EXPECT_FALSE(tr_dashboard_pressure("", "some"));
    EXPECT_FALSE(tr_dashboard_pressure("some avg10=nan", "some"));
    EXPECT_FALSE(tr_dashboard_pressure("some avg10=101", "some"));
    EXPECT_FALSE(tr_dashboard_pressure("some avg10=-1", "some"));
    EXPECT_FALSE(tr_dashboard_pressure("some avg10=2oops", "some"));
}

TEST_F(DiskProfileTest, DashboardLoadThresholdsAndMissingMeasurements)
{
    EXPECT_EQ("normal", tr_dashboard_load_state(0, 0, 0, false, false, false));
    EXPECT_EQ("unknown", tr_dashboard_load_state({}, 0, 0, false, false, false));
    EXPECT_EQ("busy", tr_dashboard_load_state(5, 0, 0, false, false, false));
    EXPECT_EQ("critical", tr_dashboard_load_state(20, 0, 0, false, false, false));
    EXPECT_EQ("critical", tr_dashboard_load_state(0, 10, 0, false, false, false));
    EXPECT_EQ("critical", tr_dashboard_load_state(0, 0, 10, false, false, false));
    EXPECT_EQ("busy", tr_dashboard_load_state({}, {}, {}, true, false, false));
    EXPECT_EQ("critical", tr_dashboard_load_state({}, {}, {}, false, false, true));
}

TEST_F(DiskProfileTest, DashboardLatencySampleExpires)
{
    tr_disk_latency latency;
    auto const now = tr_disk_latency::Clock::now();
    EXPECT_FALSE(latency.has_recent_sample(now));
    for (int i = 0; i < 15; ++i) latency.observe(150ms, now);
    EXPECT_FALSE(latency.has_recent_sample(now));
    latency.observe(150ms, now);
    EXPECT_TRUE(latency.has_recent_sample(now));
    EXPECT_EQ(tr_disk_latency::Level::Critical, latency.level(now));
    EXPECT_FALSE(latency.has_recent_sample(now + 120s));
}

class AutoDiskSessionTest : public tr::test::SessionTest
{
protected:
    void SetUp() override
    {
        settings()->get_if<tr_variant::Map>()->insert_or_assign(TR_KEY_auto_disk_profile_enabled, true);
        SessionTest::SetUp();
    }
};

TEST_F(AutoDiskSessionTest, DashboardReportsCurrentDirectoryAndCriticalIo)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    std::packaged_task<void()> task{ [&]()
    {
        for (int i = 0; i < 16; ++i)
            tor->record_disk_write(200ms, tr_disk_latency::Clock::now());
        auto response = tr_variant{};
        tr_rpc_request_exec(session_, R"({"jsonrpc":"2.0","id":1,"method":"session_stats","params":{"include_history":true}})",
            [&](tr_variant&& reply) { response = std::move(reply); });
        auto const* result = response.get_if<tr_variant::Map>()->find_if<tr_variant::Map>(TR_KEY_result);
        ASSERT_NE(nullptr, result);
        auto const* storage = result->find_if<tr_variant::Map>(tr_quark_new("storage_status"));
        ASSERT_NE(nullptr, storage);
        EXPECT_EQ("critical", storage->value_if<std::string_view>(tr_quark_new("system_state")));
        auto const* rows = storage->find_if<tr_variant::Vector>(tr_quark_new("directories"));
        ASSERT_NE(nullptr, rows);
        ASSERT_EQ(1U, rows->size());
        auto const& row = *rows->front().get_if<tr_variant::Map>();
        EXPECT_EQ(tor->current_dir().sv(), row.value_if<std::string_view>(tr_quark_new("path")));
        EXPECT_EQ("critical", row.value_if<std::string_view>(tr_quark_new("load")));
    } };
    auto done = task.get_future();
    session_->run_in_session_thread([&]() { task(); });
    done.get();
}

TEST_F(AutoDiskSessionTest, LatencyPressureLimitsNewAdmissionsAndPathRefreshResetsIt)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    auto const lock = session_->unique_lock();
    auto const before = tor->disk_profile().queue_limit(true);
    EXPECT_EQ(before, session_->count_disk_queue_free_slots(*tor, tr_direction::Down));
    auto const now = tr_disk_latency::Clock::now();
    for (int i = 0; i < 16; ++i)
    {
        tor->record_disk_write(30ms, now);
    }
    EXPECT_TRUE(tor->disk_pressured());
    EXPECT_EQ(1U, session_->count_disk_queue_free_slots(*tor, tr_direction::Down));
    EXPECT_EQ(2U, session_->count_disk_queue_free_slots(*tor, tr_direction::Up));
    tor->refresh_disk_profile(true);
    EXPECT_FALSE(tor->disk_pressured());
    EXPECT_EQ(before, session_->count_disk_queue_free_slots(*tor, tr_direction::Down));
}

TEST_F(DiskProfileTest, AutoModeRoundTripsWithoutChangingManualDefaults)
{
    auto settings = tr::SessionSettings{};
    EXPECT_FALSE(settings.auto_disk_profile_enabled);
    EXPECT_EQ(64U * 1024U, settings.disk_write_batch_size());
    settings.auto_disk_profile_enabled = true;
    auto restored = tr::SessionSettings{ tr_variant{ settings.save() } };
    EXPECT_TRUE(restored.auto_disk_profile_enabled);
}

TEST_F(DiskProfileTest, LatencyRequiresEvidenceAndCooldown)
{
    auto latency = tr_disk_latency{};
    auto const now = tr_disk_latency::Clock::now();
    latency.observe(100ms, now);
    for (int i = 0; i < 15; ++i)
    {
        latency.observe(1ms, now);
    }
    EXPECT_FALSE(latency.pressured());
    for (int i = 0; i < 15; ++i)
    {
        latency.observe(30ms, now);
    }
    EXPECT_FALSE(latency.pressured());
    latency.observe(30ms, now);
    EXPECT_TRUE(latency.pressured());
    for (int i = 0; i < 16; ++i)
    {
        latency.observe(1ms, now + 59s);
    }
    EXPECT_TRUE(latency.pressured());
    for (int i = 0; i < 16; ++i)
    {
        latency.observe(1ms, now + 60s);
    }
    EXPECT_EQ(tr_disk_latency::Level::Normal, latency.level(now + 60s));
    for (int i = 0; i < 16; ++i)
        latency.observe(1ms, now + 61s);
    EXPECT_EQ(tr_disk_latency::Level::Normal, latency.level(now + 61s));
}

TEST_F(DiskProfileTest, CriticalRecoveryIsGradualAndReadsCannotBeHiddenByWrites)
{
    using L = tr_disk_latency;
    auto latency = L{};
    auto const now = L::Clock::now();
    auto window = [&](auto elapsed, auto at, L::Operation op = L::Operation::Read)
    {
        for (int i = 0; i < 16; ++i)
            latency.observe(elapsed, at, tr_disk_profile::Kind::Ssd, op);
    };
    window(100ms, now);
    EXPECT_EQ(L::Level::Critical, latency.level(now));
    EXPECT_EQ(4U, latency.request_limit(tr_disk_profile::Kind::Ssd));
    window(1ms, now + 31s, L::Operation::Write);
    window(1ms, now + 32s, L::Operation::Write);
    EXPECT_EQ(L::Level::Critical, latency.level(now + 32s));
    window(1ms, now + 33s);
    window(1ms, now + 34s);
    EXPECT_EQ(L::Level::Busy, latency.level(now + 34s));
    window(1ms, now + 35s);
    window(1ms, now + 36s);
    EXPECT_EQ(L::Level::Busy, latency.level(now + 36s));
    window(1ms, now + 65s);
    window(1ms, now + 66s);
    EXPECT_EQ(L::Level::Normal, latency.level(now + 66s));
    window(100ms, now + 67s);
    EXPECT_EQ(L::Level::Normal, latency.level(now + 187s));
}

TEST_F(DiskProfileTest, HardwareThresholdsAndSparseSamples)
{
    using L = tr_disk_latency;
    auto hdd = L{}, ssd = L{}, sparse = L{};
    auto const now = L::Clock::now();
    for (int i = 0; i < 16; ++i)
    {
        hdd.observe(25ms, now, tr_disk_profile::Kind::Hdd);
        ssd.observe(25ms, now, tr_disk_profile::Kind::Ssd);
        sparse.observe(200ms, now + i * 11s);
    }
    EXPECT_EQ(L::Level::Normal, hdd.level(now));
    EXPECT_EQ(L::Level::Busy, ssd.level(now));
    EXPECT_EQ(L::Level::Normal, sparse.level(now + 165s));
    EXPECT_EQ(16U, ssd.request_limit(tr_disk_profile::Kind::Ssd));
}

TEST_F(DiskProfileTest, ExpiringPartialWindowPreservesPressure)
{
    auto latency = tr_disk_latency{};
    auto const now = tr_disk_latency::Clock::now();
    for (int i = 0; i < 16; ++i)
        latency.observe(200ms, now);
    latency.observe(1ms, now + 1s);
    latency.observe(1ms, now + 12s);
    EXPECT_EQ(tr_disk_latency::Level::Critical, latency.level(now + 12s));
    EXPECT_EQ(tr_disk_latency::Level::Normal, latency.level(now + 120s));
}

TEST_F(AutoDiskSessionTest, CriticalReadPressureBlocksAdmissionsAndPathRefreshResetsIt)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    auto const lock = session_->unique_lock();
    auto const now = tr_disk_latency::Clock::now();
    for (int i = 0; i < 16; ++i)
        tor->record_disk_read(200ms, now);
    EXPECT_TRUE(tor->disk_critical());
    EXPECT_EQ(0U, session_->count_disk_queue_free_slots(*tor, tr_direction::Down));
    EXPECT_EQ(0U, session_->count_disk_queue_free_slots(*tor, tr_direction::Up));
    tor->refresh_disk_profile(true);
    EXPECT_FALSE(tor->disk_critical());
}

TEST_F(DiskProfileTest, PoliciesKeepUnknownConservativeAndGroupSharedDevices)
{
    auto hdd = tr_disk_profile{ .kind = tr_disk_profile::Kind::Hdd, .devices = { "a" } };
    auto ssd = tr_disk_profile{ .kind = tr_disk_profile::Kind::Ssd, .devices = { "b" } };
    auto raid = tr_disk_profile{ .kind = tr_disk_profile::Kind::Hdd, .devices = { "a", "b" } };
    EXPECT_EQ(256U * 1024U, hdd.write_batch_size());
    EXPECT_EQ(64U * 1024U, ssd.write_batch_size());
    EXPECT_EQ(64U * 1024U, tr_disk_profile{}.write_batch_size());
    EXPECT_EQ(2U, hdd.queue_limit(true));
    EXPECT_EQ(2U, hdd.queue_limit(false));
    EXPECT_EQ(4U, ssd.queue_limit(true));
    EXPECT_FALSE(hdd.shares_device(ssd));
    EXPECT_TRUE(raid.shares_device(hdd));
    EXPECT_TRUE(raid.shares_device(ssd));
}

TEST_F(DiskProfileTest, DetectsPartitionsStacksAndUnknownWithoutWritingToDevices)
{
#ifdef __linux__
    namespace fs = std::filesystem;
    auto const root = fs::path{ sandboxDir() };
    auto write = [](fs::path const& path, std::string_view contents)
    {
        fs::create_directories(path.parent_path());
        std::ofstream{ path } << contents;
    };
    struct stat info{};
    ASSERT_EQ(0, stat(root.c_str(), &info));
    auto const key = std::to_string(major(info.st_dev)) + ':' + std::to_string(minor(info.st_dev));
    fs::create_directories(root / "sys");
    write(root / "disk/queue/rotational", "1\n");
    write(root / "disk/part/partition", "1\n");
    fs::create_directory_symlink(root / "disk/part", root / "sys" / key);
    auto const path = (root / "not-created/yet").string();
    auto const sys = (root / "sys").string();
    auto hdd = tr_detect_disk_profile(path, sys);
    ASSERT_EQ(tr_disk_profile::Kind::Hdd, hdd.kind);
    ASSERT_EQ(1U, hdd.devices.size());
    EXPECT_EQ((root / "disk").string(), hdd.devices[0]);
    write(root / "disk/queue/rotational", "0\n");
    EXPECT_EQ(tr_disk_profile::Kind::Ssd, tr_detect_disk_profile(path, sys).kind);
    write(root / "disk/device/model", "Virtual Disk\n");
    EXPECT_EQ(tr_disk_profile::Kind::Unknown, tr_detect_disk_profile(path, sys).kind);
    write(root / "disk/device/model", "physical test fixture\n");
    write(root / "disk/queue/rotational", "broken\n");
    EXPECT_EQ(tr_disk_profile::Kind::Unknown, tr_detect_disk_profile(path, sys).kind);
    write(root / "disk/queue/rotational", "0\n");
    write(root / "other/queue/rotational", "1\n");
    fs::create_directories(root / "stack/slaves");
    fs::create_directory_symlink(root / "disk", root / "stack/slaves/a");
    fs::create_directory_symlink(root / "other", root / "stack/slaves/b");
    fs::remove(root / "sys" / key);
    fs::create_directory_symlink(root / "stack", root / "sys" / key);
    auto stacked = tr_detect_disk_profile(path, sys);
    EXPECT_EQ(tr_disk_profile::Kind::Hdd, stacked.kind);
    EXPECT_TRUE(stacked.shares_device(hdd));
    EXPECT_EQ(2U, stacked.devices.size());
    fs::create_directory_symlink(root / "stack", root / "stack/slaves/cycle");
    EXPECT_EQ(tr_disk_profile::Kind::Unknown, tr_detect_disk_profile(path, sys).kind);
    EXPECT_EQ(tr_disk_profile::Kind::Unknown, tr_detect_disk_profile(path, (root / "absent").string()).kind);
#else
    GTEST_SKIP() << "Linux sysfs detector";
#endif
}
