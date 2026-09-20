// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>
#include <fmt/format.h>
#include "libtransmission/log.h"

enum class tr_security_event : size_t { RpcAuth, RpcBlocked, RpcAccess, PeerMessage, FilePath, Count };

struct tr_security_log_window
{
    using Clock = std::chrono::steady_clock;
    Clock::time_point until{};
    unsigned emitted = 0;
    uint64_t suppressed = 0;
    bool admit(Clock::time_point now, uint64_t& previous)
    {
        previous = 0;
        if (now >= until)
        {
            previous = suppressed;
            suppressed = 0;
            emitted = 0;
            until = now + std::chrono::seconds{ 60 };
        }
        if (emitted < 5) { ++emitted; return true; }
        if (suppressed != UINT64_MAX) ++suppressed;
        return false;
    }
};

// Fixed storage, shared across threads; never index by attacker-controlled text.
// Details supplied by callers contain only event codes, numeric lengths/errno
// and parsed socket addresses, never request headers, payloads or file paths.
inline void tr_security_log(tr_security_event event, std::string details)
{
    static std::mutex mutex;
    static std::array<tr_security_log_window, static_cast<size_t>(tr_security_event::Count)> windows;
    bool last;
    uint64_t previous;
    {
        auto lock = std::lock_guard{ mutex };
        auto& window = windows[static_cast<size_t>(event)];
        if (!window.admit(tr_security_log_window::Clock::now(), previous)) return;
        last = window.emitted == 5;
    }
    if (previous) details += fmt::format("; previous_window_suppressed={}", previous);
    if (last) details += "; further events of this category aggregated for up to 60s";
    tr_logAddWarn(std::move(details), "Security");
}
