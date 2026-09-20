// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <algorithm>
#include <chrono>
#include <map>
#include <memory>
#include <span>
#include <string>
#include <type_traits>
#include <utility>
#include "libtransmission/block-info.h"
#include "libtransmission/disk-profile.h"
#include "libtransmission/error-types.h"

struct tr_session;
struct tr_torrent;

// Session-thread only. Pending blocks are not advertised, hashed or saved as
// complete until write() succeeds. Fixed-size regions bound reserved payload RAM.
class tr_disk_cache
{
public:
    using Clock = std::chrono::steady_clock;
    static constexpr size_t BlocksPerRegion = 32;
    static constexpr size_t RegionSize = BlocksPerRegion * tr_block_info::BlockSize;
    // Sixteen MiB still coalesces 32-block regions at full line speed while
    // leaving more cgroup headroom for the kernel's reclaimable file cache.
    static constexpr size_t Capacity = 16U * 1024U * 1024U;
    explicit tr_disk_cache(tr_session& session, size_t capacity = Capacity)
        : session_{ session }
        , capacity_{ std::clamp(capacity / RegionSize, size_t{ 1 }, Capacity / RegionSize) * RegionSize }
    {
    }
    template<typename Done>
    tr_error_code_t add(tr_torrent& tor, tr_block_index_t block, std::span<uint8_t const> data, Done&& done)
    {
        using Callback = std::decay_t<Done>;
        return add(
            tor,
            block,
            data,
            std::make_shared<Callback>(std::forward<Done>(done)),
            [](void* owner, tr_torrent&, tr_block_index_t) { (*static_cast<Callback*>(owner))(); });
    }
    using OnWritten = void (*)(void*, tr_torrent&, tr_block_index_t);
    tr_error_code_t add(
        tr_torrent& tor,
        tr_block_index_t block,
        std::span<uint8_t const> data,
        std::shared_ptr<void> owner,
        OnWritten on_written);
    tr_error_code_t flush(tr_torrent_id_t id);
    // End of a receive batch: publish completed pieces without waiting for a timer.
    tr_error_code_t flush_ready(tr_torrent_id_t id);
    void discard(tr_torrent_id_t id);
    void pulse(Clock::time_point now = Clock::now());
    [[nodiscard]] size_t reserved_bytes() const noexcept
    {
        return entries_.size() * RegionSize;
    }
    [[nodiscard]] size_t pending_bytes() const noexcept
    {
        return pending_bytes_;
    }
    [[nodiscard]] size_t allocated_bytes() const noexcept
    {
        return reserved_bytes() + (spare_ ? RegionSize : 0U);
    }
    [[nodiscard]] bool congested() const noexcept
    {
        return (!entries_.empty() && overloaded_) || reserved_bytes() >= capacity_ * 3 / 4;
    }
    [[nodiscard]] size_t download_limit(tr_disk_profile const& profile) const noexcept;

    struct Admission
    {
        size_t limit = 0, samples = 0, fast = 0, slow = 0;
        Clock::time_point next_increase{};
        void observe(tr_disk_profile::Kind kind, Clock::duration elapsed, bool room, Clock::time_point now);
    };

private:
    using Key = std::pair<tr_torrent_id_t, tr_block_index_t>;
    struct Entry
    {
        std::unique_ptr<uint8_t[]> data;
        uint32_t present = 0;
        struct Completion
        {
            std::shared_ptr<void> owner;
            OnWritten on_written = nullptr;
        };
        // Keep the sender alive without a separately allocated closure per block.
        std::array<Completion, BlocksPerRegion> done;
        Clock::time_point created = Clock::now();
        size_t bytes = 0;
        bool ready = false;
    };
    using Entries = std::map<Key, Entry>;
    tr_error_code_t flush_entry(Entries::iterator it);
    tr_session& session_;
    size_t capacity_;
    Entries entries_;
    std::map<std::string, Admission> admissions_;
    size_t pending_bytes_ = 0;
    bool flushing_ = false;
    bool overloaded_ = false;
    // Reuse one recently drained region to avoid mmap/free churn on fast disks.
    std::unique_ptr<uint8_t[]> spare_;
    Clock::time_point spare_since_{};
};
