// SPDX-License-Identifier: GPL-2.0-or-later
#include "libtransmission/disk-cache.h"
#include "libtransmission/inout.h"
#include "libtransmission/session.h"
#include "libtransmission/torrent.h"
#include <algorithm>
#include <bit>
#include <cerrno>
#include <new>
#include <utility>

using namespace std::chrono_literals;

void tr_disk_cache::Admission::observe(tr_disk_profile::Kind kind, Clock::duration elapsed, bool room, Clock::time_point now)
{
    auto const initial = kind == tr_disk_profile::Kind::Ssd ? 4U : 2U;
    auto const maximum = kind == tr_disk_profile::Kind::Hdd ? 4U : (kind == tr_disk_profile::Kind::Ssd ? 8U : 2U);
    if (limit == 0)
    {
        limit = initial;
        next_increase = now + 30s;
    }
    auto const threshold = kind == tr_disk_profile::Kind::Hdd ? 30ms : 20ms;
    ++samples;
    fast += elapsed < threshold / 4;
    slow += elapsed >= threshold;
    if (samples < 16)
        return;
    if (slow >= 8 || !room)
    {
        limit = std::max<size_t>(1, limit / 2);
        next_increase = now + 30s;
    }
    else if (fast >= 14 && now >= next_increase)
    {
        limit = std::min<size_t>(maximum, limit + 1);
        next_increase = now + 30s;
    }
    samples = fast = slow = 0;
}

size_t tr_disk_cache::download_limit(tr_disk_profile const& profile) const noexcept
{
    auto const initial = profile.kind == tr_disk_profile::Kind::Ssd ? 4U : 2U;
    auto limit = std::numeric_limits<size_t>::max();
    for (auto const& device : profile.devices)
    {
        if (auto const it = admissions_.find(device); it != admissions_.end())
        {
            limit = std::min(limit, it->second.limit);
        }
        else
        {
            limit = std::min<size_t>(limit, initial);
        }
    }
    return congested() ? 1U : (profile.devices.empty() ? initial : limit);
}

tr_error_code_t tr_disk_cache::add(
    tr_torrent& tor,
    tr_block_index_t block,
    std::span<uint8_t const> data,
    std::shared_ptr<void> owner,
    OnWritten on_written)
{
    if (block >= tor.block_count() || data.size() != tor.block_size(block))
        return EINVAL;
    auto const key = Key{ tor.id(), block / BlocksPerRegion };
    auto it = entries_.find(key);
    if (it == entries_.end())
    {
        if (reserved_bytes() >= capacity_)
        {
            auto oldest = std::min_element(
                entries_.begin(),
                entries_.end(),
                [](auto const& a, auto const& b) { return a.second.created < b.second.created; });
            auto const evicted_id = oldest->first.first;
            auto const err = flush_entry(oldest);
            if (err != 0 && evicted_id == tor.id())
                return err;
        }
        auto storage = spare_ ? std::move(spare_) : std::unique_ptr<uint8_t[]>{ new (std::nothrow) uint8_t[RegionSize] };
        if (!storage)
        {
            if (auto const err = flush(tor.id()); err != 0)
                return err;
            auto const err = tr_ioWrite(tor, session_.openFiles(), tor.block_loc(block), data);
            if (err == 0)
                on_written(owner.get(), tor, block);
            return err;
        }
        it = entries_.try_emplace(key).first;
        it->second.data = std::move(storage);
    }
    auto& entry = it->second;
    auto const slot = block % BlocksPerRegion;
    if ((entry.present & (uint32_t{ 1 } << slot)) != 0)
        return 0; // Endgame duplicate: first receipt owns completion.
    std::copy(data.begin(), data.end(), entry.data.get() + slot * tr_block_info::BlockSize);
    entry.present |= uint32_t{ 1 } << slot;
    entry.done[slot] = { std::move(owner), on_written };
    entry.bytes += data.size();
    pending_bytes_ += data.size();
    auto const expected = std::min<size_t>(BlocksPerRegion, tor.block_count() - key.second * BlocksPerRegion);
    // Complete moderate-sized pieces promptly so hashing and request refill do
    // not wait for an unrelated adjacent piece or a timer tick.
    auto const piece = tor.block_loc(block).piece;
    auto const span = tor.block_info().block_span_for_piece(piece);
    auto piece_ready = span.end - span.begin >= 4 && span.begin / BlocksPerRegion == key.second &&
        (span.end - 1) / BlocksPerRegion == key.second;
    if (piece_ready)
    {
        auto const count = span.end - span.begin;
        auto const mask = (UINT32_MAX >> (BlocksPerRegion - count)) << (span.begin % BlocksPerRegion);
        auto missing = mask & ~entry.present;
        // Usually all blocks are in RAM. Only consult committed completion for
        // holes left by an earlier sibling-piece flush; stop at the first gap.
        while (missing != 0)
        {
            if (!tor.has_block(key.second * BlocksPerRegion + std::countr_zero(missing)))
            {
                piece_ready = false;
                break;
            }
            missing &= missing - 1;
        }
        // Coalesce an interleaved sibling only within the current receive batch.
        // flush_ready() publishes completed pieces at the batch boundary.
        entry.ready = entry.ready || piece_ready;
        piece_ready = piece_ready && (entry.present & ~mask) == 0;
    }
    return static_cast<size_t>(std::popcount(entry.present)) == expected || piece_ready ? flush_entry(it) : 0;
}

tr_error_code_t tr_disk_cache::flush_entry(Entries::iterator it)
{
    // Remove before I/O: a write error synchronously stops the torrent, and
    // completion callbacks can close or rename files. Neither may revisit this entry.
    auto node = entries_.extract(it);
    pending_bytes_ -= node.mapped().bytes;
    auto& entry = node.mapped();
    auto const id = node.key().first;
    auto* tor = session_.torrents().get(id);
    if (tor == nullptr)
        return ENOENT;
    auto const was_flushing = std::exchange(flushing_, true);
    auto error = tr_error_code_t{};
    for (size_t first = 0; first < BlocksPerRegion;)
    {
        if ((entry.present & (uint32_t{ 1 } << first)) == 0 || tor->has_block(node.key().second * BlocksPerRegion + first))
        {
            ++first;
            continue;
        }
        auto end = first + 1;
        while (end < BlocksPerRegion && (entry.present & (uint32_t{ 1 } << end)) != 0 &&
               !tor->has_block(node.key().second * BlocksPerRegion + end))
            ++end;
        // Validation at insertion guarantees full blocks except the torrent tail.
        auto const length = (end - first - 1) * tr_block_info::BlockSize +
            tor->block_size(node.key().second * BlocksPerRegion + end - 1);
        auto const started = Clock::now();
        error = tr_ioWrite(
            *tor,
            session_.openFiles(),
            tor->block_loc(node.key().second * BlocksPerRegion + first),
            std::span<uint8_t const>{ entry.data.get() + first * tr_block_info::BlockSize, length });
        auto const ended = Clock::now();
        if (error != 0)
            break;
        tor->record_disk_write(ended - started, ended);
        for (auto const& device : tor->disk_profile().devices)
            admissions_[device].observe(tor->disk_profile().kind, ended - started, reserved_bytes() < capacity_ / 2, ended);
        for (auto i = first; i < end; ++i)
            entry.done[i].on_written(entry.done[i].owner.get(), *tor, node.key().second * BlocksPerRegion + i);
        first = end;
    }
    flushing_ = was_flushing;
    if (error != 0)
        discard(id);
    if (!spare_)
    {
        spare_ = std::move(entry.data);
        spare_since_ = Clock::now();
    }
    return error;
}

tr_error_code_t tr_disk_cache::flush_ready(tr_torrent_id_t id)
{
    if (flushing_)
        return 0;
    for (;;)
    {
        auto it = entries_.lower_bound(Key{ id, 0 });
        while (it != entries_.end() && it->first.first == id && !it->second.ready)
            ++it;
        if (it == entries_.end() || it->first.first != id)
            return 0;
        if (auto const error = flush_entry(it); error != 0)
            return error;
    }
}

tr_error_code_t tr_disk_cache::flush(tr_torrent_id_t id)
{
    if (flushing_)
        return 0;
    for (;;)
    {
        auto const it = entries_.lower_bound(Key{ id, 0 });
        if (it == entries_.end() || it->first.first != id)
            return 0;
        if (auto const err = flush_entry(it); err != 0)
            return err;
    }
}

void tr_disk_cache::discard(tr_torrent_id_t id)
{
    auto it = entries_.lower_bound(Key{ id, 0 });
    while (it != entries_.end() && it->first.first == id)
    {
        pending_bytes_ -= it->second.bytes;
        it = entries_.erase(it);
    }
}

void tr_disk_cache::pulse(Clock::time_point now)
{
    if (spare_ && now - spare_since_ >= 2s)
        spare_.reset();
    if (flushing_)
        return;
    auto const deadline = Clock::now() + 10ms;
    for (size_t flushed = 0; flushed < 8; ++flushed)
    {
        auto const it = std::min_element(
            entries_.begin(),
            entries_.end(),
            [](auto const& a, auto const& b) { return a.second.created < b.second.created; });
        if (it == entries_.end() || now - it->second.created < 250ms)
            break;
        flush_entry(it);
        if (Clock::now() >= deadline)
            break;
    }
    overloaded_ = std::ranges::any_of(entries_, [now](auto const& item) { return now - item.second.created >= 1s; });
}
