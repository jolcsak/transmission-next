// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <chrono>
#include <map>
#include <string>
#include <string_view>

class tr_rpc_login_limiter
{
public:
    using Clock = std::chrono::steady_clock;
    bool blocked(std::string const& host, Clock::time_point now)
    {
        auto it = hosts_.find(host);
        if (it == hosts_.end()) return false;
        if (now >= it->second.expires)
        {
            hosts_.erase(it);
            return false;
        }
        return it->second.blocked;
    }
    void failed(std::string const& host, size_t limit, Clock::time_point now)
    {
        blocked(host, now); // expire a previous observation window
        if (!hosts_.contains(host) && hosts_.size() >= 128)
        {
            auto oldest = std::min_element(hosts_.begin(), hosts_.end(),
                [](auto const& a, auto const& b) { return a.second.expires < b.second.expires; });
            hosts_.erase(oldest);
        }
        auto [it, inserted] = hosts_.try_emplace(host);
        auto& entry = it->second;
        if (inserted) entry.expires = now + std::chrono::seconds{ 60 };
        if (++entry.failures >= std::max(size_t{ 1 }, limit))
        {
            entry.blocked = true;
            entry.expires = now + std::chrono::seconds{ 30 };
        }
    }
    void success(std::string const& host) { hosts_.erase(host); }
    void clear() { hosts_.clear(); }
private:
    struct Entry { size_t failures = 0; bool blocked = false; Clock::time_point expires; };
    std::map<std::string, Entry> hosts_;
};
