// This file Copyright (C) 2013-2022 Mnemosyne LLC.
// It may be used under GPLv2 (SPDX: GPL-2.0-only), GPLv3 (SPDX: GPL-3.0-only),
// or any future license endorsed by Mnemosyne LLC.
// License text can be found in the licenses/ folder.

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <string_view>
#include <vector>

#ifndef _WIN32
#include <unistd.h>
#endif

#include <gtest/gtest.h>

#include <libtransmission/error.h>
#include <libtransmission/file-utils.h>
#include <libtransmission/file.h>
#include <libtransmission/tr-strbuf.h>
#include <libtransmission/variant.h>

#include "test-fixtures.h"

using UtilsTest = ::tr::test::TransmissionTest;
using namespace std::literals;

TEST_F(UtilsTest, SaveIfChangedPreservesIdenticalFile)
{
    auto sandbox = tr::test::Sandbox{};
    auto const path = sandbox.path() + "/state.json";
    auto error = tr_error{};
    auto const data = std::string(40000, 'x');
    ASSERT_TRUE(tr_file_save_if_changed(path, data, &error));
    auto const old_time = std::filesystem::file_time_type::clock::now() - std::chrono::hours{ 24 };
    std::filesystem::last_write_time(path, old_time);
    auto const saved_time = std::filesystem::last_write_time(path);
    for (int i = 0; i < 10; ++i)
    {
        EXPECT_TRUE(tr_file_save_if_changed(path, data, &error));
        EXPECT_FALSE(error);
        EXPECT_EQ(saved_time, std::filesystem::last_write_time(path));
    }
    EXPECT_EQ(1, std::distance(std::filesystem::directory_iterator{ sandbox.path() }, std::filesystem::directory_iterator{}));
}

TEST_F(UtilsTest, SaveIfChangedHandlesChangesEmptyAndMissingFiles)
{
    auto sandbox = tr::test::Sandbox{};
    auto const path = sandbox.path() + "/state.json";
    for (auto const& data :
         { std::string{}, std::string(40000, 'a'), std::string(40000, 'b'), std::string{ "short" }, std::string{} })
    {
        auto error = tr_error{};
        ASSERT_TRUE(tr_file_save_if_changed(path, data, &error));
        EXPECT_FALSE(error);
        auto actual = std::vector<char>{};
        ASSERT_TRUE(tr_file_read(path, actual, &error));
        EXPECT_EQ(data, std::string(actual.begin(), actual.end()));
    }
    EXPECT_TRUE(tr_sys_path_remove(path));
    EXPECT_TRUE(tr_file_save_if_changed(path, "restored"));
    auto error = tr_error{};
    EXPECT_FALSE(tr_file_save_if_changed(sandbox.path() + "/missing/state.json", "data", &error));
    EXPECT_TRUE(error);
}

TEST_F(UtilsTest, DISABLED_BenchmarkUnchangedStateSaves)
{
#ifdef _WIN32
    GTEST_SKIP() << "Linux storage measurement";
#else
    auto const* directory = std::getenv("TR_SSD_BENCH_DIR");
    ASSERT_NE(nullptr, directory);
    auto const path = std::string{ directory } + "/state.json";
    auto value = tr_variant{ std::string(16384, 'x') };
    auto serde = tr_variant_serde::json();
    auto const baseline = std::getenv("TR_SSD_BASELINE") != nullptr;
    auto const flush = std::getenv("TR_SSD_FLUSH") != nullptr;
    auto const start = std::chrono::steady_clock::now();
    for (int i = 0; i < 1000; ++i)
    {
        ASSERT_TRUE(baseline ? tr_file_save(path, serde.to_string(value)) : serde.to_file(value, path));
        if (flush)
        {
            auto const fd = tr_sys_file_open(path, TR_SYS_FILE_READ, 0);
            ASSERT_NE(TR_BAD_SYS_FILE, fd);
            EXPECT_EQ(0, fsync(fd));
            EXPECT_TRUE(tr_sys_file_close(fd));
        }
    }
    fmt::print(
        "SSD_STATE_BENCH_MS {}\n",
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count());
    auto loaded = tr_variant_serde::json().parse_file(path);
    ASSERT_TRUE(loaded);
    EXPECT_EQ(std::string(16384, 'x'), loaded->value_if<std::string_view>().value_or(""sv));
#endif
}

TEST_F(UtilsTest, saveFile)
{
    auto filename = tr_pathbuf{};

    // save a file to GoogleTest's temp dir
    auto const sandbox = tr::test::Sandbox::createSandbox(::testing::TempDir(), "transmission-test-XXXXXX");
    filename.assign(sandbox, "filename.txt"sv);
    auto contents = "these are the contents"sv;
    auto error = tr_error{};
    EXPECT_TRUE(tr_file_save(filename.sv(), contents, &error));
    EXPECT_FALSE(error) << error;

    // now read the file back in and confirm the contents are the same
    auto buf = std::vector<char>{};
    EXPECT_TRUE(tr_file_read(filename.sv(), buf, &error));
    EXPECT_FALSE(error) << error;
    auto sv = std::string_view{ std::data(buf), std::size(buf) };
    EXPECT_EQ(contents, sv);

    // remove the tempfile
    EXPECT_TRUE(tr_sys_path_remove(filename, &error));
    EXPECT_FALSE(error) << error;

    // try saving a file to a path that doesn't exist
    filename = "/this/path/does/not/exist/foo.txt";
    EXPECT_FALSE(tr_file_save(filename.sv(), contents, &error));
    ASSERT_TRUE(error);
    EXPECT_NE(0, error.code());
}
