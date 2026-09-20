// SPDX-License-Identifier: GPL-2.0-or-later
#include "test-fixtures.h"
#include "libtransmission/disk-cache.h"
#include "libtransmission/inout.h"
#include "libtransmission/crypto-utils.h"
#include <array>
#include <future>
#include <filesystem>
#include <fstream>

class DiskCacheTest : public tr::test::SessionTest
{
protected:
    tr_torrent* two_piece_torrent()
    {
        auto const data = std::vector<uint8_t>(262144);
        auto const hash = tr_sha1::digest(data);
        auto meta = std::string{ "d4:infod6:lengthi524288e4:name5:cache12:piece lengthi262144e6:pieces40:" };
        meta.append(reinterpret_cast<char const*>(hash.data()), hash.size());
        meta.append(reinterpret_cast<char const*>(hash.data()), hash.size());
        meta += "ee";
        auto* ctor = tr_ctorNew(session_);
        tr_error error;
        if (!tr_ctorSetMetainfo(ctor, meta.data(), meta.size(), &error))
        {
            tr_ctorFree(ctor);
            return nullptr;
        }
        tr_ctorSetPaused(ctor, TR_FORCE, true);
        auto* tor = createTorrentAndWaitForVerifyDone(ctor);
        tr_ctorFree(ctor);
        return tor;
    }

    template<typename Func>
    void in_session(Func&& func)
    {
        std::packaged_task<void()> task{ std::forward<Func>(func) };
        auto done = task.get_future();
        session_->run_in_session_thread([&task]() { task(); });
        done.get();
    }
};

TEST_F(DiskCacheTest, InterleavedPiecesCoalesceWithoutEarlySiblingWrite)
{
    auto* tor = two_piece_torrent();
    ASSERT_NE(nullptr, tor);
    in_session([&]() {
        auto& cache = session_->disk_cache();
        std::array<uint8_t, tr_block_info::BlockSize> data{};
        int completed = 0;
        auto add = [&](tr_block_index_t b) {
            EXPECT_EQ(0, cache.add(*tor, b, data, [&, b]() { ++completed; tor->on_block_received(b); }));
        };
        add(0);
        for (tr_block_index_t b = 16; b < 32; ++b) add(b);
        EXPECT_EQ(0, completed);
        EXPECT_EQ(17U * tr_block_info::BlockSize, cache.pending_bytes());
        for (tr_block_index_t b = 1; b < 16; ++b) add(b);
        EXPECT_EQ(32, completed);
        EXPECT_EQ(0U, cache.pending_bytes());
        EXPECT_TRUE(tr_ioTestPiece(*tor, 0));
        EXPECT_TRUE(tr_ioTestPiece(*tor, 1));
    });
}

TEST_F(DiskCacheTest, InterleavedIncompleteRegionStillHonorsAgeLimit)
{
    auto* tor = two_piece_torrent();
    ASSERT_NE(nullptr, tor);
    in_session([&]() {
        auto& cache = session_->disk_cache();
        std::array<uint8_t, tr_block_info::BlockSize> data{};
        for (auto b : {0U, 16U})
            EXPECT_EQ(0, cache.add(*tor, b, data, [tor, b]() { tor->on_block_received(b); }));
        EXPECT_FALSE(tor->has_block(0));
        cache.pulse(tr_disk_cache::Clock::now() + 1s);
        EXPECT_TRUE(tor->has_block(0));
        EXPECT_TRUE(tor->has_block(16));
        EXPECT_EQ(0U, cache.pending_bytes());
    });
}

TEST_F(DiskCacheTest, InterestRevisionTracksDataSelectionAndVerification)
{
    auto* tor = two_piece_torrent();
    ASSERT_NE(nullptr, tor);
    in_session([&]() {
        auto revision = tor->interest_revision();
        tor->on_block_received(0);
        EXPECT_GT(tor->interest_revision(), revision);
        revision = tor->interest_revision();
        tr_file_index_t file = 0;
        tor->set_files_wanted(&file, 1, false);
        EXPECT_GT(tor->interest_revision(), revision);
        revision = tor->interest_revision();
        tr_torrent::VerifyMediator{tor}.on_piece_checked(0, false);
        EXPECT_GT(tor->interest_revision(), revision);
    });
}

TEST_F(DiskCacheTest, SenderLivesUntilFlushOrDiscard)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            auto& cache = session_->disk_cache();
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            int completions = 0;
            auto sender = std::make_shared<int*>(&completions);
            std::weak_ptr<int*> weak = sender;
            auto written = [](void* owner, tr_torrent& torrent, tr_block_index_t block)
            {
                ++**static_cast<int**>(owner);
                torrent.on_block_received(block);
            };
            ASSERT_EQ(0, cache.add(*tor, 0, data, sender, written));
            sender.reset();
            EXPECT_FALSE(weak.expired());
            EXPECT_EQ(0, completions);
            ASSERT_EQ(0, cache.flush(tor->id()));
            EXPECT_TRUE(weak.expired());
            EXPECT_EQ(1, completions);
            EXPECT_TRUE(tor->has_block(0));

            sender = std::make_shared<int*>(&completions);
            weak = sender;
            ASSERT_EQ(0, cache.add(*tor, 1, data, sender, written));
            sender.reset();
            cache.discard(tor->id());
            EXPECT_TRUE(weak.expired());
            EXPECT_EQ(1, completions);
            EXPECT_FALSE(tor->has_block(1));
        });
}

TEST_F(DiskCacheTest, DefersCompletionAndDeduplicatesUntilTimedFlush)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            auto& cache = session_->disk_cache();
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            int completions = 0;
            auto done = [&]()
            {
                ++completions;
                tor->on_block_received(0);
            };
            EXPECT_EQ(0, cache.add(*tor, 0, data, done));
            EXPECT_EQ(0, cache.add(*tor, 0, data, done));
            EXPECT_EQ(0, completions);
            EXPECT_FALSE(tor->has_block(0));
            EXPECT_EQ(data.size(), cache.pending_bytes());
            EXPECT_EQ(tr_disk_cache::RegionSize, cache.reserved_bytes());
            cache.pulse(tr_disk_cache::Clock::now() + 1s);
            EXPECT_EQ(1, completions);
            EXPECT_TRUE(tor->has_block(0));
            EXPECT_EQ(0U, cache.pending_bytes());
            EXPECT_EQ(0U, cache.reserved_bytes());
            std::array<uint8_t, tr_block_info::BlockSize> read;
            read.fill(255);
            EXPECT_EQ(0, tr_ioRead(*tor, session_->openFiles(), tor->block_loc(0), read));
            EXPECT_EQ(data, read);
            EXPECT_EQ(tr_disk_cache::RegionSize, cache.allocated_bytes());
            cache.pulse(tr_disk_cache::Clock::now() + 3s);
            EXPECT_EQ(0U, cache.allocated_bytes());
        });
}

TEST_F(DiskCacheTest, ReverseBlocksCompleteAndVerifyMultifileTorrent)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            for (auto b = tor->block_count(); b > 0;)
            {
                --b;
                EXPECT_EQ(
                    0,
                    session_->disk_cache()
                        .add(*tor, b, std::span{ data }.first(tor->block_size(b)), [tor, b]() { tor->on_block_received(b); }));
            }
            EXPECT_EQ(0, session_->disk_cache().flush(tor->id()));
            for (tr_piece_index_t p = 0; p < tor->piece_count(); ++p)
            {
                EXPECT_TRUE(tor->has_piece(p));
                EXPECT_TRUE(tr_ioTestPiece(*tor, p));
            }
        });
}

TEST_F(DiskCacheTest, CapacityEvictsOldestAndDiscardDoesNotAcknowledge)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            tr_disk_cache cache{ *session_, tr_disk_cache::RegionSize };
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            int first = 0, second = 0;
            EXPECT_EQ(0, cache.add(*tor, 0, data, [&]() { ++first; }));
            EXPECT_TRUE(cache.congested());
            EXPECT_EQ(0, cache.add(*tor, 32, data, [&]() { ++second; }));
            EXPECT_EQ(1, first);
            EXPECT_EQ(0, second);
            EXPECT_EQ(tr_disk_cache::RegionSize, cache.reserved_bytes());
            cache.discard(tor->id());
            EXPECT_EQ(0, second);
            EXPECT_EQ(0U, cache.reserved_bytes());
            EXPECT_EQ(0U, cache.pending_bytes());
            EXPECT_LE(cache.allocated_bytes(), tr_disk_cache::RegionSize);
        });
}

TEST_F(DiskCacheTest, StopFlushesPendingBlocks)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            bool completed = false;
            EXPECT_EQ(
                0,
                session_->disk_cache().add(
                    *tor,
                    0,
                    data,
                    [&]()
                    {
                        completed = true;
                        tor->on_block_received(0);
                    }));
            tr_torrentStop(tor);
            EXPECT_TRUE(completed);
            EXPECT_TRUE(tor->has_block(0));
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
        });
}

TEST_F(DiskCacheTest, MoveFlushesToOldLocationBeforeMoving)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    auto const target = std::filesystem::path{ sandboxDir() } / "moved";
    std::filesystem::create_directory(target);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            bool completed = false;
            EXPECT_EQ(
                0,
                session_->disk_cache().add(
                    *tor,
                    0,
                    data,
                    [&]()
                    {
                        completed = true;
                        tor->on_block_received(0);
                    }));
            int state = 0;
            tor->set_location(target.string(), true, &state);
            EXPECT_EQ(TR_LOC_DONE, state);
            EXPECT_TRUE(completed);
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
            auto const found = tor->find_file(0);
            ASSERT_TRUE(found.has_value());
            EXPECT_TRUE(std::string{ found->filename() }.starts_with(target.string()));
        });
}

TEST_F(DiskCacheTest, CompletedBlockCannotBeOverwrittenByPendingData)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> bad;
            bad.fill(255);
            std::array<uint8_t, tr_block_info::BlockSize> good{};
            EXPECT_EQ(0, session_->disk_cache().add(*tor, 0, bad, []() {}));
            EXPECT_EQ(0, tr_ioWrite(*tor, session_->openFiles(), tor->block_loc(0), good));
            tor->on_block_received(0);
            EXPECT_EQ(0, session_->disk_cache().flush(tor->id()));
            EXPECT_EQ(0, tr_ioRead(*tor, session_->openFiles(), tor->block_loc(0), bad));
            EXPECT_EQ(good, bad);
        });
}

TEST_F(DiskCacheTest, WriteFailureDiscardsUncommittedBlocksAndDoesNotAcknowledge)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            auto const blocked_path = std::filesystem::path{ tor->current_dir().sv() } / std::string{ tor->name() };
            std::ofstream{ blocked_path } << "not a directory";
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            int completed = 0;
            EXPECT_EQ(0, session_->disk_cache().add(*tor, 0, data, [&]() { ++completed; }));
            EXPECT_EQ(0, session_->disk_cache().add(*tor, 32, data, [&]() { ++completed; }));
            EXPECT_NE(0, session_->disk_cache().flush(tor->id()));
            EXPECT_EQ(0, completed);
            EXPECT_FALSE(tor->has_block(0));
            EXPECT_FALSE(tor->has_block(32));
            EXPECT_TRUE(tor->error().is_local_error());
            EXPECT_EQ(0U, session_->disk_cache().reserved_bytes());
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
        });
}

TEST_F(DiskCacheTest, PartiallyCommittedPieceDoesNotWaitForTimer)
{
    auto const piece_data = std::vector<uint8_t>(262144);
    auto const hash = tr_sha1::digest(piece_data);
    auto meta = std::string{ "d4:infod6:lengthi524288e4:name5:cache12:piece lengthi262144e6:pieces40:" };
    meta.append(reinterpret_cast<char const*>(hash.data()), hash.size());
    meta.append(reinterpret_cast<char const*>(hash.data()), hash.size());
    meta += "ee";
    auto* ctor = tr_ctorNew(session_);
    tr_error error;
    ASSERT_TRUE(tr_ctorSetMetainfo(ctor, meta.data(), meta.size(), &error));
    tr_ctorSetPaused(ctor, TR_FORCE, true);
    auto* tor = createTorrentAndWaitForVerifyDone(ctor);
    tr_ctorFree(ctor);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            auto add = [&](tr_block_index_t b)
            {
                EXPECT_EQ(0, session_->disk_cache().add(*tor, b, data, [tor, b]() { tor->on_block_received(b); }));
            };
            add(0);
            for (tr_block_index_t b = 16; b < 32; ++b)
                add(b);
            // Explicitly create a partially committed sibling; interleaved
            // arrivals now stay together until the region fills or ages out.
            ASSERT_EQ(0, session_->disk_cache().flush_ready(tor->id()));
            EXPECT_TRUE(tor->has_block(0));
            EXPECT_TRUE(tor->has_piece(1));
            for (tr_block_index_t b = 1; b < 16; ++b)
                add(b);
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
            EXPECT_TRUE(tor->has_piece(0));
        });
}

TEST_F(DiskCacheTest, FullWidthPieceMaskFlushesWithoutTimer)
{
    auto const piece_data = std::vector<uint8_t>(tr_disk_cache::RegionSize);
    auto const hash = tr_sha1::digest(piece_data);
    auto meta = std::string{ "d4:infod6:lengthi524288e4:name5:cache12:piece lengthi524288e6:pieces20:" };
    meta.append(reinterpret_cast<char const*>(hash.data()), hash.size());
    meta += "ee";
    auto* ctor = tr_ctorNew(session_);
    tr_error error;
    ASSERT_TRUE(tr_ctorSetMetainfo(ctor, meta.data(), meta.size(), &error));
    tr_ctorSetPaused(ctor, TR_FORCE, true);
    auto* tor = createTorrentAndWaitForVerifyDone(ctor);
    tr_ctorFree(ctor);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            for (tr_block_index_t b = 32; b > 0;)
            {
                --b;
                ASSERT_EQ(0, session_->disk_cache().add(*tor, b, data, [tor, b]() { tor->on_block_received(b); }));
                EXPECT_EQ(b == 0, tor->has_piece(0));
            }
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
            EXPECT_TRUE(tr_ioTestPiece(*tor, 0));
        });
}

TEST_F(DiskCacheTest, DirectDirectorySetterFlushesBeforeChangingPaths)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    auto const target = std::filesystem::path{ sandboxDir() } / "direct-directory";
    std::filesystem::create_directory(target);
    bool completed = false;
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            EXPECT_EQ(
                0,
                session_->disk_cache().add(
                    *tor,
                    0,
                    data,
                    [&]()
                    {
                        completed = true;
                        tor->on_block_received(0);
                    }));
        });
    tr_torrentSetDownloadDir(tor, target.string());
    in_session(
        [&]()
        {
            EXPECT_TRUE(completed);
            EXPECT_EQ(target.string(), tor->download_dir().sv());
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
        });
}

TEST_F(DiskCacheTest, RenameFlushesPendingBlocks)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    in_session(
        [&]()
        {
            std::array<uint8_t, tr_block_info::BlockSize> data{};
            bool completed = false, renamed = false;
            EXPECT_EQ(
                0,
                session_->disk_cache().add(
                    *tor,
                    0,
                    data,
                    [&]()
                    {
                        completed = true;
                        tor->on_block_received(0);
                    }));
            tor->rename_path(tor->file_subpath(0), "renamed", [&](auto, auto, auto, auto const& error) { renamed = !error; });
            EXPECT_TRUE(completed);
            EXPECT_TRUE(renamed);
            EXPECT_EQ(0U, session_->disk_cache().pending_bytes());
            auto const found = tor->find_file(0);
            EXPECT_TRUE(found.has_value()) << "current=" << tor->current_dir().sv() << " download=" << tor->download_dir().sv()
                                           << " incomplete=" << tor->incomplete_dir().sv() << " file=" << tor->file_subpath(0);
            if (!found)
                for (auto const& item : std::filesystem::recursive_directory_iterator(sandboxDir()))
                    ADD_FAILURE() << item.path().string();
        });
}

TEST(DiskCacheAdmissionTest, RampsSlowlyAndBacksOffOnPressure)
{
    using Kind = tr_disk_profile::Kind;
    auto const now = tr_disk_cache::Clock::now();
    tr_disk_cache::Admission state;
    auto window = [&](auto elapsed, auto at, bool room = true)
    {
        for (int i = 0; i < 16; ++i)
            state.observe(Kind::Hdd, elapsed, room, at);
    };
    window(1ms, now);
    EXPECT_EQ(2U, state.limit);
    window(1ms, now + 29s);
    EXPECT_EQ(2U, state.limit);
    window(1ms, now + 30s);
    EXPECT_EQ(3U, state.limit);
    window(1ms, now + 60s);
    EXPECT_EQ(4U, state.limit);
    window(1ms, now + 90s);
    EXPECT_EQ(4U, state.limit);
    window(40ms, now + 91s);
    EXPECT_EQ(2U, state.limit);
    window(1ms, now + 92s, false);
    EXPECT_EQ(1U, state.limit);
    window(1ms, now + 100s);
    EXPECT_EQ(1U, state.limit);
}
