// This file Copyright © Mnemosyne LLC.
// It may be used under GPLv2 (SPDX: GPL-2.0-only), GPLv3 (SPDX: GPL-3.0-only),
// or any future license endorsed by Mnemosyne LLC.
// License text can be found in the licenses/ folder.

#include <chrono>
#include <future>
#include <numeric>
#include <string>
#include <vector>

#include <libtransmission/inout.h>
#include <libtransmission/rpcimpl.h>

#include "test-fixtures.h"

namespace tr::test
{
class OptimizationTest : public SessionTest
{
protected:
    tr_torrent* makeTorrent(std::string const& name, size_t piece_size, std::vector<size_t> const& sizes)
    {
        auto const total = std::accumulate(sizes.begin(), sizes.end(), size_t{});
        auto data = std::string(total, '\0');
        for (size_t i = 0; i < total; ++i)
        {
            data[i] = static_cast<char>((i * 31U + (i >> 8U)) & 255U);
        }
        auto pieces = std::string{};
        for (size_t i = 0; i < total; i += piece_size)
        {
            auto const hash = tr_sha1::digest(std::string_view{ data }.substr(i, piece_size));
            pieces.append(reinterpret_cast<char const*>(hash.data()), hash.size());
        }

        auto benc = std::string{ "d4:infod5:filesl" };
        size_t offset = 0;
        for (size_t i = 0; i < sizes.size(); ++i)
        {
            auto const filename = fmt::format("file-{}", i);
            benc += fmt::format("d6:lengthi{}e4:pathl{}:{}ee", sizes[i], filename.size(), filename);
            auto const path = fmt::format("{}/{}/{}", tr_sessionGetDownloadDir(session_), name, filename);
            buildParentDir(path);
            auto fd = tr_sys_file_open(path, TR_SYS_FILE_WRITE | TR_SYS_FILE_CREATE | TR_SYS_FILE_TRUNCATE, 0600);
            EXPECT_NE(fd, TR_BAD_SYS_FILE);
            blockingFileWrite(fd, data.data() + offset, sizes[i]);
            tr_sys_file_close(fd);
            offset += sizes[i];
        }
        benc += fmt::format("e4:name{}:{}12:piece lengthi{}e6:pieces{}:", name.size(), name, piece_size, pieces.size());
        benc += pieces;
        benc += "ee";
        auto ctor = tr_ctor{ session_ };
        auto error = tr_error{};
        EXPECT_TRUE(tr_ctorSetMetainfo(&ctor, benc.data(), benc.size(), &error)) << error;
        tr_ctorSetPaused(&ctor, TR_FORCE, true);
        return createTorrentAndWaitForVerifyDone(&ctor);
    }

    void inSession(std::function<void()> const& func)
    {
        auto done = std::promise<void>{};
        auto future = done.get_future();
        session_->run_in_session_thread(
            [&]()
            {
                func();
                done.set_value();
            });
        future.get();
    }

    tr_variant request(std::string_view json)
    {
        auto req = tr_variant_serde::json().parse(json);
        EXPECT_TRUE(req);
        auto response = tr_variant{};
        tr_rpc_request_exec(session_, *req, [&response](tr_variant&& value) { response = std::move(value); });
        return response;
    }
};

TEST_F(OptimizationTest, PieceHashAcrossFilesAndUnalignedPieces)
{
    for (auto piece_size : { 10003U, 300007U })
    {
        auto* tor = makeTorrent(fmt::format("unaligned-{}", piece_size), piece_size, { 0, 17001, 0, 910007, 0, 29 });
        ASSERT_NE(tor, nullptr);
        inSession(
            [&]()
            {
                for (tr_piece_index_t piece = 0; piece < tor->piece_count(); ++piece)
                {
                    EXPECT_TRUE(tr_ioTestPiece(*tor, piece)) << piece;
                }
            });
    }
}

TEST_F(OptimizationTest, LargePieceHashAndCorruption)
{
    auto* tor = makeTorrent("large", 4U * 1024U * 1024U, { 300001, 0, 5U * 1024U * 1024U + 17U });
    ASSERT_NE(tor, nullptr);
    inSession(
        [&]()
        {
            for (tr_piece_index_t piece = 0; piece < tor->piece_count(); ++piece)
            {
                EXPECT_TRUE(tr_ioTestPiece(*tor, piece));
            }
            auto const filename = fmt::format("{}/large/file-2", tr_sessionGetDownloadDir(session_));
            auto fd = tr_sys_file_open(filename, TR_SYS_FILE_WRITE, 0600);
            ASSERT_NE(fd, TR_BAD_SYS_FILE);
            char const changed = '\x7f';
            ASSERT_TRUE(tr_sys_file_write_at(fd, &changed, 1, 1234, nullptr));
            tr_sys_file_close(fd);
            EXPECT_FALSE(tr_ioTestPiece(*tor, 0));
            EXPECT_TRUE(tr_ioTestPiece(*tor, 1));
        });
}

TEST_F(OptimizationTest, MissingPieceFileFails)
{
    auto* tor = zeroTorrentInit(ZeroTorrentState::NoFiles);
    ASSERT_NE(tor, nullptr);
    inSession([&]() { EXPECT_FALSE(tr_ioTestPiece(*tor, 0)); });
}

TEST_F(OptimizationTest, UnexpectedEofIsAnIoError)
{
    auto* tor = makeTorrent("truncated-io", 16384, { 32768 });
    ASSERT_NE(tor, nullptr);
    inSession(
        [&]()
        {
            auto const filename = fmt::format("{}/truncated-io/file-0", tr_sessionGetDownloadDir(session_));
            auto fd = tr_sys_file_open(filename, TR_SYS_FILE_WRITE, 0600);
            ASSERT_NE(fd, TR_BAD_SYS_FILE);
            ASSERT_TRUE(tr_sys_file_truncate(fd, 99));
            tr_sys_file_close(fd);
            auto data = std::vector<uint8_t>(16384);
            EXPECT_NE(0, tr_ioRead(*tor, session_->openFiles(), tor->byte_loc(0), data));
            EXPECT_FALSE(tr_ioTestPiece(*tor, 0));
        });
}

TEST_F(OptimizationTest, RpcStaticAndMixedFieldsInBothFormats)
{
    auto* tor = makeTorrent("rpc-fields", 32768, { 100003 });
    ASSERT_NE(tor, nullptr);
    inSession(
        [&]()
        {
            for (bool mixed : { false, true })
            {
                auto const fields = mixed ? R"("name","rate_download","id","status","total_size","peers_from")" :
                                            R"("id","name","hash_string","total_size")";
                auto const
                    object_json = std::string{ R"({"jsonrpc":"2.0","method":"torrent_get","id":1,"params":{"fields":[)" } +
                    fields + "]}}";
                auto const table_json =
                    std::string{ R"({"jsonrpc":"2.0","method":"torrent_get","id":1,"params":{"format":"table","fields":[)" } +
                    fields + "]}}";
                auto object = request(object_json);
                auto table = request(table_json);
                auto* object_map = object.get_if<tr_variant::Map>();
                auto* table_map = table.get_if<tr_variant::Map>();
                ASSERT_NE(object_map, nullptr);
                ASSERT_NE(table_map, nullptr);
                auto* object_result = object_map->find_if<tr_variant::Map>(TR_KEY_result);
                auto* table_result = table_map->find_if<tr_variant::Map>(TR_KEY_result);
                ASSERT_NE(object_result, nullptr);
                ASSERT_NE(table_result, nullptr);
                auto* objects = object_result->find_if<tr_variant::Vector>(TR_KEY_torrents);
                auto* rows = table_result->find_if<tr_variant::Vector>(TR_KEY_torrents);
                ASSERT_NE(objects, nullptr);
                ASSERT_NE(rows, nullptr);
                ASSERT_EQ(objects->size(), 1U);
                ASSERT_EQ(rows->size(), 2U);
                auto* info = objects->front().get_if<tr_variant::Map>();
                auto* names = rows->front().get_if<tr_variant::Vector>();
                auto* values = rows->back().get_if<tr_variant::Vector>();
                ASSERT_NE(info, nullptr);
                ASSERT_NE(names, nullptr);
                ASSERT_NE(values, nullptr);
                EXPECT_EQ(info->value_if<int64_t>(TR_KEY_id), tor->id());
                EXPECT_EQ(info->value_if<std::string_view>(TR_KEY_name), tor->name());
                EXPECT_EQ(info->value_if<int64_t>(TR_KEY_total_size), 100003);
                if (mixed)
                {
                    EXPECT_EQ(info->value_if<int64_t>(TR_KEY_rate_download), 0);
                    EXPECT_EQ(info->value_if<int64_t>(TR_KEY_status), TR_STATUS_STOPPED);
                    EXPECT_NE(info->find_if<tr_variant::Map>(TR_KEY_peers_from), nullptr);
                }
                ASSERT_EQ(names->size(), values->size());
                for (size_t i = 0; i < names->size(); ++i)
                {
                    auto const key = tr_quark_lookup(*(*names)[i].value_if<std::string_view>());
                    ASSERT_TRUE(key);
                    auto const it = info->find(*key);
                    ASSERT_NE(it, info->end());
                    EXPECT_EQ(tr_variant_serde::json().to_string(it->second), tr_variant_serde::json().to_string((*values)[i]));
                }
            }
        });
}

// Opt in with --gtest_also_run_disabled_tests --gtest_filter=OptimizationTest.DISABLED_Benchmark.
TEST_F(OptimizationTest, DISABLED_Benchmark)
{
    auto measure = [](std::string_view name, size_t iterations, size_t bytes, auto&& operation)
    {
        operation(); // warm the code, file handles and page cache
        for (int sample = 0; sample < 7; ++sample)
        {
            auto const start = std::chrono::steady_clock::now();
            for (size_t i = 0; i < iterations; ++i)
            {
                operation();
            }
            auto const seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            fmt::print(
                "BENCH {{\"name\":\"{}\",\"sample\":{},\"iterations\":{},\"bytes\":{},\"seconds\":{:.9f}}}\n",
                name,
                sample,
                iterations,
                bytes,
                seconds);
        }
    };
    for (auto piece_size : { 16384U, 262144U, 4194304U })
    {
        auto* tor = makeTorrent(fmt::format("hash-{}", piece_size), piece_size, { 64U * 1024U * 1024U + 137U });
        ASSERT_NE(tor, nullptr);
        inSession(
            [&]()
            {
                measure(
                    fmt::format("hash_{}", piece_size),
                    4,
                    tor->total_size() * 4,
                    [&]()
                    {
                        for (tr_piece_index_t p = 0; p < tor->piece_count(); ++p)
                        {
                            ASSERT_TRUE(tr_ioTestPiece(*tor, p));
                        }
                    });
            });
    }
    for (size_t i = 0; i < 1000; ++i)
    {
        auto ctor = tr_ctor{ session_ };
        ASSERT_TRUE(ctor.set_metainfo_from_magnet_link(fmt::format("magnet:?xt=urn:btih:{:040x}&dn=bench-{}", i + 1, i)));
        tr_ctorSetPaused(&ctor, TR_FORCE, true);
        ASSERT_NE(tr_torrentNew(&ctor, nullptr), nullptr);
    }
    inSession(
        [&]()
        {
            for (auto const format : { "object"sv, "table"sv })
            {
                for (bool mixed : { false, true })
                {
                    auto const fields = mixed ?
                        R"("id","name","rate_download","rate_upload","status","peers_connected","percent_done","eta")" :
                        R"("id","name","hash_string","total_size")";
                    auto const json = std::string{ R"({"jsonrpc":"2.0","id":1,"method":"torrent_get","params":{"format":")" } +
                        std::string{ format } + R"(","fields":[)" + fields + "]}}";
                    auto req = tr_variant_serde::json().parse(json);
                    ASSERT_TRUE(req);
                    auto response = tr_variant{};
                    auto operation = [&]()
                    {
                        tr_rpc_request_exec(session_, *req, [&response](tr_variant&& value) { response = std::move(value); });
                    };
                    measure(fmt::format("rpc_{}_{}", mixed ? "mixed" : "static", format), 200, 0, operation);
                    auto* map = response.get_if<tr_variant::Map>();
                    ASSERT_NE(map, nullptr);
                    ASSERT_NE(map->find_if<tr_variant::Map>(TR_KEY_result), nullptr);
                    auto const serialized = tr_variant_serde::json().to_string(response);
                    auto const digest = tr_sha1::digest(serialized);
                    fmt::print(
                        "CHECK rpc_{}_{} {} {}\n",
                        mixed ? "mixed" : "static",
                        format,
                        serialized.size(),
                        tr_sha1_to_string(digest));
                }
            }
        });
}
} // namespace tr::test
