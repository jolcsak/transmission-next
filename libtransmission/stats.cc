// This file Copyright © Mnemosyne LLC.
// It may be used under GPLv2 (SPDX: GPL-2.0-only), GPLv3 (SPDX: GPL-3.0-only),
// or any future license endorsed by Mnemosyne LLC.
// License text can be found in the licenses/ folder.

#include <initializer_list>
#include <optional>
#include <utility>

#include "libtransmission/api-compat.h"
#include "libtransmission/error.h"
#include "libtransmission/file.h"
#include "libtransmission/quark.h"
#include "libtransmission/serializer.h"
#include "libtransmission/stats.h"
#include "libtransmission/tr-strbuf.h"
#include "libtransmission/utils.h" // for tr_getRatio(), tr_time()
#include "libtransmission/variant.h"

using namespace std::literals;
using namespace tr;

namespace
{
template<auto MemberPtr>
using Field = serializer::Field<MemberPtr>;

constexpr auto Fields = std::tuple{
    Field<&tr_session_stats::downloadedBytes>{ TR_KEY_downloaded_bytes },
    Field<&tr_session_stats::filesAdded>{ TR_KEY_files_added },
    Field<&tr_session_stats::secondsActive>{ TR_KEY_seconds_active },
    Field<&tr_session_stats::sessionCount>{ TR_KEY_session_count },
    Field<&tr_session_stats::uploadedBytes>{ TR_KEY_uploaded_bytes },
};
} // namespace

tr_session_stats tr_stats::load_old_stats(std::string_view const config_dir)
{
    auto var = std::optional<tr_variant>{};

    if (auto file = tr_pathbuf{ config_dir, "/stats.json"sv }; tr_sys_path_exists(file))
    {
        var = tr_variant_serde::json().parse_file(file);
    }
    else if (auto oldfile = tr_pathbuf{ config_dir, "/stats.benc"sv }; tr_sys_path_exists(oldfile))
    {
        var = tr_variant_serde::benc().parse_file(oldfile);
    }

    if (!var)
    {
        return {};
    }

    api_compat::convert_incoming_data(*var);
    auto ret = tr_session_stats{};
    serializer::load(ret, Fields, *var);
    return ret;
}

bool tr_stats::save() const
{
    auto var = tr_variant{ serializer::save(cumulative(), Fields) };
    var.get_if<tr_variant::Map>()->try_emplace(tr_quark_new("transfer_history"), history());
    api_compat::convert_outgoing_data(var);
    return tr_variant_serde::json().to_file(var, tr_pathbuf{ config_dir_, "/stats.json"sv });
}

void tr_stats::save_if_dirty(Clock::time_point const now)
{
    // Resume files keep their own cadence; only cumulative statistics wait.
    if (!is_dirty_ || now - last_save_ < SaveInterval)
    {
        return;
    }

    if (save())
    {
        is_dirty_ = false;
        last_save_ = now;
    }
}

void tr_stats::clear()
{
    single_ = old_ = Zero;
    days_ = {};
    hours_ = {};
    history_started_ = tr_time();
    start_time_ = tr_time();
    is_dirty_ = true;
}

[[nodiscard]] tr_session_stats tr_stats::current() const
{
    auto ret = single_;
    ret.secondsActive = time(nullptr) - start_time_;
    ret.ratio = tr_getRatio(ret.uploadedBytes, ret.downloadedBytes);
    return ret;
}

tr_session_stats tr_stats::add(tr_session_stats const& a, tr_session_stats const& b)
{
    auto ret = tr_session_stats{};
    ret.uploadedBytes = a.uploadedBytes + b.uploadedBytes;
    ret.downloadedBytes = a.downloadedBytes + b.downloadedBytes;
    ret.filesAdded = a.filesAdded + b.filesAdded;
    ret.sessionCount = a.sessionCount + b.sessionCount;
    ret.secondsActive = a.secondsActive + b.secondsActive;
    ret.ratio = tr_getRatio(ret.uploadedBytes, ret.downloadedBytes);
    return ret;
}

void tr_stats::record(uint32_t up, uint32_t down, time_t now) noexcept
{
    if (now <= 0 || (up == 0 && down == 0))
        return;
    auto const update = [=](auto& buckets, int64_t interval)
    {
        auto const index = int64_t{ now } / interval;
        auto& bucket = buckets[static_cast<size_t>(index) % buckets.size()];
        auto const start = index * interval;
        // A backward clock must not evict a newer bucket in the same ring slot.
        if (bucket.start > start)
            return;
        if (bucket.start != start)
            bucket = Bucket{ start, 0, 0 };
        bucket.up += up;
        bucket.down += down;
    };
    update(days_, 86400);
    update(hours_, 3600);
}

tr_variant tr_stats::history(time_t now) const
{
    auto result = tr_variant::Map{};
    result.try_emplace(tr_quark_new("started_at"), history_started_);
    result.try_emplace(tr_quark_new("now"), now);
    result.try_emplace(tr_quark_new("timezone"), "UTC");
    auto const pack = [now](auto const& buckets, int64_t interval)
    {
        auto list = tr_variant::Vector{};
        for (auto const& bucket : buckets)
        {
            if (bucket.start <= 0 || bucket.start > now ||
                bucket.start < (now / interval - int64_t{ buckets.size() } + 1) * interval)
                continue;
            auto row = tr_variant::Vector{};
            row.emplace_back(bucket.start);
            row.emplace_back(bucket.down);
            row.emplace_back(bucket.up);
            list.emplace_back(std::move(row));
        }
        return list;
    };
    result.try_emplace(tr_quark_new("days"), pack(days_, 86400));
    result.try_emplace(tr_quark_new("hours"), pack(hours_, 3600));
    return result;
}

void tr_stats::load_history(time_t now)
{
    history_started_ = now;
    auto const filename = tr_pathbuf{ config_dir_, "/stats.json"sv };
    auto error = tr_error{};
    if (!tr_sys_path_get_info(filename, 0, &error) && tr_error_is_enoent(error.code()))
    {
        // A new profile has no persisted statistics yet. Other filesystem
        // errors are left to parse_file(), which reports them normally.
        return;
    }

    auto value = tr_variant_serde::json().parse_file(filename);
    auto const* root = value ? value->get_if<tr_variant::Map>() : nullptr;
    auto const* history = root ? root->find_if<tr_variant::Map>(tr_quark_new("transfer_history")) : nullptr;
    if (history == nullptr)
        return;
    auto const started = history->value_if<int64_t>(tr_quark_new("started_at")).value_or(0);
    if (started <= 0 || started > now)
        return;
    history_started_ = started;
    auto const unpack = [&](auto& buckets, char const* key, int64_t interval)
    {
        auto const* rows = history->find_if<tr_variant::Vector>(tr_quark_new(key));
        if (rows == nullptr || rows->size() > buckets.size())
            return;
        for (auto const& item : *rows)
        {
            auto const* row = item.get_if<tr_variant::Vector>();
            if (row == nullptr || row->size() != 3)
                continue;
            auto const start = (*row)[0].value_if<int64_t>().value_or(0);
            auto const down = (*row)[1].value_if<int64_t>().value_or(-1);
            auto const up = (*row)[2].value_if<int64_t>().value_or(-1);
            if (start <= 0 || start > now || start % interval != 0 || down < 0 || up < 0 ||
                start < (now / interval - int64_t{ buckets.size() } + 1) * interval)
                continue;
            buckets[static_cast<size_t>(start / interval) % buckets.size()] =
                Bucket{ start, static_cast<uint64_t>(up), static_cast<uint64_t>(down) };
        }
    };
    unpack(days_, "days", 86400);
    unpack(hours_, "hours", 3600);
}
