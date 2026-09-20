// This file Copyright © Mnemosyne LLC.
// SPDX-License-Identifier: GPL-2.0-or-later

#include <chrono>
#include <fstream>
#include <future>
#include <numeric>
#include <string>
#include <vector>

#include <libtransmission/verify.h>

#include "test-fixtures.h"

namespace tr::test
{
class DiskOptimizationTest : public SandboxedTest
{
protected:
    struct Result
    {
        std::vector<bool> pieces;
        std::promise<void> done;
        bool aborted = false;
    };

    class Mediator final : public tr_verify_worker::Mediator
    {
    public:
        Mediator(tr_torrent_metainfo const& info, std::vector<std::string> const& paths, Result& result)
            : info_{ info }
            , paths_{ paths }
            , result_{ result }
        {
        }
        tr_torrent_metainfo const& metainfo() const override
        {
            return info_;
        }
        std::optional<std::string> find_file(tr_file_index_t index) const override
        {
            return paths_.at(index);
        }
        void on_verify_queued() override
        {
        }
        void on_verify_started() override
        {
        }
        void on_piece_checked(tr_piece_index_t piece, bool valid) override
        {
            result_.pieces.at(piece) = valid;
        }
        void on_verify_done(bool aborted) override
        {
            result_.aborted = aborted;
            result_.done.set_value();
        }

    private:
        tr_torrent_metainfo const& info_;
        std::vector<std::string> const& paths_;
        Result& result_;
    };

    void makeFiles(size_t piece_size, std::vector<size_t> const& sizes)
    {
        paths_.clear();
        auto data = std::string(std::accumulate(sizes.begin(), sizes.end(), size_t{}), '\0');
        for (size_t i = 0; i < data.size(); ++i)
        {
            data[i] = static_cast<char>((i * 31U + (i >> 8U)) & 255U);
        }
        auto hashes = std::string{};
        for (size_t i = 0; i < data.size(); i += piece_size)
        {
            auto hash = tr_sha1::digest(std::string_view{ data }.substr(i, piece_size));
            hashes.append(reinterpret_cast<char const*>(hash.data()), hash.size());
        }
        auto benc = std::string{ "d4:infod5:filesl" };
        size_t offset = 0;
        for (size_t i = 0; i < sizes.size(); ++i)
        {
            auto name = fmt::format("file-{}", i);
            auto path = fmt::format("{}/{}", sandboxDir(), name);
            paths_.push_back(path);
            auto fd = tr_sys_file_open(path, TR_SYS_FILE_WRITE | TR_SYS_FILE_CREATE | TR_SYS_FILE_TRUNCATE, 0600);
            ASSERT_NE(TR_BAD_SYS_FILE, fd);
            blockingFileWrite(fd, data.data() + offset, sizes[i]);
            tr_sys_file_close(fd);
            offset += sizes[i];
            benc += fmt::format("d6:lengthi{}e4:pathl{}:{}ee", sizes[i], name.size(), name);
        }
        benc += fmt::format("e4:name4:test12:piece lengthi{}e6:pieces{}:", piece_size, hashes.size());
        benc += hashes + "ee";
        auto error = tr_error{};
        ASSERT_TRUE(info_.parse_benc(benc, &error)) << error;
    }

    std::vector<bool> verify(double* elapsed_ms = nullptr)
    {
        auto result = Result{};
        result.pieces.resize(info_.piece_count());
        auto done = result.done.get_future();
        auto worker = tr_verify_worker{};
        auto const start = std::chrono::steady_clock::now();
        worker.add(std::make_unique<Mediator>(info_, paths_, result), TR_PRI_NORMAL);
        done.get();
        if (elapsed_ms != nullptr)
        {
            *elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        }
        EXPECT_FALSE(result.aborted);
        return result.pieces;
    }

    void expectAllValid()
    {
        auto pieces = verify();
        EXPECT_EQ(info_.piece_count(), pieces.size());
        EXPECT_TRUE(std::all_of(pieces.begin(), pieces.end(), [](bool b) { return b; }));
    }

    static uint64_t readCalls()
    {
        // This observation itself costs one read, subtracted by the benchmark.
        auto input = std::ifstream{ "/proc/self/io" };
        std::string key;
        uint64_t value = 0;
        while (input >> key >> value)
        {
            if (key == "syscr:")
                return value;
        }
        return 0;
    }

    void benchmark(size_t piece_size)
    {
        makeFiles(piece_size, { 128U * 1024U * 1024U });
        expectAllValid();
        for (int sample = 0; sample < 9; ++sample)
        {
            auto const reads = readCalls();
            auto ms = double{};
            auto const pieces = verify(&ms);
            EXPECT_TRUE(std::all_of(pieces.begin(), pieces.end(), [](bool b) { return b; }));
            auto const after = readCalls();
            fmt::print("DISK_BENCH {} {} {:.6f} {}\n", piece_size, sample, ms, after > reads ? after - reads - 1 : 0);
        }
    }

    tr_torrent_metainfo info_;
    std::vector<std::string> paths_;
};

TEST_F(DiskOptimizationTest, SmallAndUnalignedPiecesAcrossFiles)
{
    for (auto size : { 16384U, 10003U, 300007U })
    {
        makeFiles(size, { 0, 17001, 0, 910007, 0, 29 });
        expectAllValid();
    }
}

TEST_F(DiskOptimizationTest, CorruptionDoesNotPoisonFollowingPieces)
{
    makeFiles(16384, { 1024U * 1024U });
    auto fd = tr_sys_file_open(paths_[0], TR_SYS_FILE_WRITE, 0600);
    ASSERT_NE(fd, TR_BAD_SYS_FILE);
    char const bad = 1;
    ASSERT_TRUE(tr_sys_file_write_at(fd, &bad, 1, 0, nullptr));
    tr_sys_file_close(fd);
    auto pieces = verify();
    ASSERT_FALSE(pieces[0]);
    for (size_t i = 1; i < pieces.size(); ++i)
        EXPECT_TRUE(pieces[i]);
}

TEST_F(DiskOptimizationTest, TruncatedAndMissingFiles)
{
    makeFiles(16384, { 32768, 32768, 32768 });
    auto fd = tr_sys_file_open(paths_[1], TR_SYS_FILE_WRITE, 0600);
    ASSERT_NE(fd, TR_BAD_SYS_FILE);
    ASSERT_TRUE(tr_sys_file_truncate(fd, 16384 + 99));
    tr_sys_file_close(fd);
    auto pieces = verify();
    EXPECT_EQ((std::vector<bool>{ true, true, true, false, true, true }), pieces);
    ASSERT_TRUE(tr_sys_path_remove(paths_[1]));
    EXPECT_EQ((std::vector<bool>{ true, true, false, false, true, true }), verify());
}

TEST_F(DiskOptimizationTest, ShortFileCannotMatchHashOfOnlyItsPrefix)
{
    makeFiles(16384, { 16384 });
    auto fd = tr_sys_file_open(paths_[0], TR_SYS_FILE_WRITE, 0600);
    ASSERT_NE(fd, TR_BAD_SYS_FILE);
    ASSERT_TRUE(tr_sys_file_truncate(fd, 99));
    tr_sys_file_close(fd);
    auto prefix = std::string(99, '\0');
    for (size_t i = 0; i < prefix.size(); ++i)
        prefix[i] = static_cast<char>((i * 31U + (i >> 8U)) & 255U);
    auto hash = tr_sha1::digest(prefix);
    auto benc = std::string{ "d4:infod6:lengthi16384e4:name4:test12:piece lengthi16384e6:pieces20:" };
    benc.append(reinterpret_cast<char const*>(hash.data()), hash.size());
    benc += "ee";
    ASSERT_TRUE(info_.parse_benc(benc));
    EXPECT_EQ((std::vector<bool>{ false }), verify());
}

TEST_F(DiskOptimizationTest, DISABLED_Benchmark16KiB)
{
    benchmark(16384);
}
TEST_F(DiskOptimizationTest, DISABLED_Benchmark4MiB)
{
    benchmark(4U * 1024U * 1024U);
}
} // namespace tr::test
