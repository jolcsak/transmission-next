// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <istream>
#include <optional>
#include <sstream>
#include <string>

struct tr_vpn_traffic
{
    int64_t received_bytes;
    int64_t received_packets;
    int64_t sent_bytes;
    int64_t sent_packets;
};

// /proc/net/dev is scoped to the caller's network namespace, unlike sysfs
// mounts which can still describe the host namespace after setns().
inline std::optional<tr_vpn_traffic> tr_read_vpn_traffic(std::istream& input)
{
    std::string line;
    while (std::getline(input, line))
    {
        auto const colon = line.find(':');
        if (colon == std::string::npos || line.substr(0, colon).find_first_not_of(" \t") == std::string::npos)
        {
            continue;
        }
        auto name = line.substr(0, colon);
        name.erase(0, name.find_first_not_of(" \t"));
        if (name != "tun0")
        {
            continue;
        }
        std::istringstream fields{ line.substr(colon + 1) };
        tr_vpn_traffic result{};
        int64_t ignored = 0;
        if (!(fields >> result.received_bytes >> result.received_packets))
        {
            return std::nullopt;
        }
        for (int i = 0; i < 6; ++i)
        {
            if (!(fields >> ignored))
            {
                return std::nullopt;
            }
        }
        if (!(fields >> result.sent_bytes >> result.sent_packets) || result.received_bytes < 0 ||
            result.received_packets < 0 || result.sent_bytes < 0 || result.sent_packets < 0)
        {
            return std::nullopt;
        }
        return result;
    }
    return std::nullopt;
}
