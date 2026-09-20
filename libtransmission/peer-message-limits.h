// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <optional>

// Wire lengths include the message ID. Check before accumulating any payload.
[[nodiscard]] constexpr bool tr_peer_message_length_valid(
    uint8_t id, uint32_t length, std::optional<uint32_t> piece_count)
{
    switch (id)
    {
    case 0: case 1: case 2: case 3: case 14: case 15:
        return length == 1;
    case 4: case 13: case 17:
        return length == 5;
    case 5:
        return piece_count ? length == 1ULL + (uint64_t{ *piece_count } + 7) / 8 :
                             length >= 1 && length <= 1024U * 1024U;
    case 6: case 8: case 16:
        return length == 13;
    case 7:
        return length >= 9 && length <= 9 + 16384;
    case 9:
        return length == 3;
    case 20:
        // BEP 9 metadata blocks are 16 KiB. Leave ample room for extension
        // dictionaries and PEX without accepting an unbounded remote length.
        return length >= 2 && length <= 256U * 1024U;
    default:
        return false;
    }
}
