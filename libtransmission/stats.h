// This file Copyright © Mnemosyne LLC.
// It may be used under GPLv2 (SPDX: GPL-2.0-only), GPLv3 (SPDX: GPL-3.0-only),
// or any future license endorsed by Mnemosyne LLC.
// License text can be found in the licenses/ folder.

#pragma once

#ifndef __TRANSMISSION__
#error only libtransmission should #include this header.
#endif

#include <chrono>
#include <array>
#include <cstdint>
#include <ctime>
#include <string>
#include <string_view>

#include "libtransmission/types.h" // for tr_session_stats
#include "libtransmission/variant.h"
#include "libtransmission/utils.h"

// per-session data structure for bandwidth use statistics
class tr_stats
{
public:
    using Clock = std::chrono::steady_clock;
    static constexpr auto SaveInterval = std::chrono::minutes{ 30 };

    tr_stats(std::string_view config_dir, time_t now, Clock::time_point checkpoint = Clock::now())
        : config_dir_{ config_dir }
        , start_time_{ now }
        , last_save_{ checkpoint }
        , is_dirty_{ true }
    {
        single_.sessionCount = 1;
        old_ = load_old_stats(config_dir_);
        load_history(now);
    }

    ~tr_stats()
    {
        save();
    }

    tr_stats(tr_stats const&) = delete;
    tr_stats(tr_stats&&) = delete;
    tr_stats& operator=(tr_stats const&) = delete;
    tr_stats& operator=(tr_stats&&) = delete;

    void clear();

    [[nodiscard]] tr_session_stats current() const;

    [[nodiscard]] auto cumulative() const
    {
        return add(current(), old_);
    }

    void add_uploaded(uint32_t n_bytes, time_t now = tr_time()) noexcept
    {
        single_.uploadedBytes += n_bytes;
        record(n_bytes, 0, now);
        is_dirty_ = true;
    }

    void add_downloaded(uint32_t n_bytes, time_t now = tr_time()) noexcept
    {
        single_.downloadedBytes += n_bytes;
        record(0, n_bytes, now);
        is_dirty_ = true;
    }

    constexpr void add_file_created() noexcept
    {
        ++single_.filesAdded;
        is_dirty_ = true;
    }

    bool save() const;
    void save_if_dirty(Clock::time_point now = Clock::now());

    // Fixed-size UTC buckets: no per-block allocation or extra checkpoint writes.
    [[nodiscard]] tr_variant history(time_t now = tr_time()) const;

private:
    struct Bucket
    {
        int64_t start = 0;
        uint64_t up = 0;
        uint64_t down = 0;
    };
    void record(uint32_t up, uint32_t down, time_t now) noexcept;
    void load_history(time_t now);
    std::array<Bucket, 62> days_{};
    std::array<Bucket, 48> hours_{};
    time_t history_started_ = 0;
    static tr_session_stats add(tr_session_stats const& a, tr_session_stats const& b);

    static tr_session_stats load_old_stats(std::string_view config_dir);

    std::string const config_dir_;
    time_t start_time_;
    Clock::time_point last_save_;

    static constexpr auto Zero = tr_session_stats{
        .ratio = TR_RATIO_NA,
        .uploadedBytes = 0U,
        .downloadedBytes = 0U,
        .filesAdded = 0U,
        .sessionCount = 0U,
        .secondsActive = time_t{},
    };
    tr_session_stats single_ = Zero;
    tr_session_stats old_ = Zero;
    bool is_dirty_ = false;
};
