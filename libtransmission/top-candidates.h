// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <span>
#include <utility>

// Streaming equivalent of retaining the first Capacity entries of partial_sort.
// Storage is bounded independently of the number of discovered peers.
template<typename T, size_t Capacity, typename Compare>
class tr_top_candidates
{
    static_assert(Capacity > 0);

public:
    explicit tr_top_candidates(Compare compare) : compare_{ std::move(compare) } {}

    void push(T value)
    {
        if (size_ < Capacity)
        {
            entries_[size_++] = std::move(value);
            if (size_ == Capacity)
            {
                std::make_heap(entries_.begin(), entries_.end(), compare_);
            }
        }
        else if (compare_(value, entries_.front()))
        {
            std::pop_heap(entries_.begin(), entries_.end(), compare_);
            entries_.back() = std::move(value);
            std::push_heap(entries_.begin(), entries_.end(), compare_);
        }
    }

    // Finalize once, after the last push. The returned span borrows this object.
    [[nodiscard]] std::span<T const> finish()
    {
        auto const end = entries_.begin() + size_;
        if (size_ == Capacity)
        {
            std::sort_heap(entries_.begin(), end, compare_);
        }
        else
        {
            std::sort(entries_.begin(), end, compare_);
        }
        return { entries_.data(), size_ };
    }

private:
    std::array<T, Capacity> entries_{};
    size_t size_ = 0;
    Compare compare_;
};
