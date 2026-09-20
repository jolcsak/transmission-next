// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cmath>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>

// Linux PSI is stall time, not CPU utilization. Missing/malformed is unknown.
inline std::optional<double> tr_dashboard_pressure(std::string_view text, std::string_view category)
{
    std::istringstream input{ std::string{ text } };
    std::string line;
    while (std::getline(input, line))
    {
        std::istringstream fields{ line };
        std::string kind, value;
        fields >> kind;
        if (kind != category)
            continue;
        while (fields >> value)
        {
            if (!value.starts_with("avg10="))
                continue;
            std::istringstream number{ value.substr(6) };
            double result = 0;
            if ((number >> result) && number.peek() == std::char_traits<char>::eof() &&
                std::isfinite(result) && result >= 0 && result <= 100)
                return result;
            return std::nullopt;
        }
    }
    return std::nullopt;
}

inline std::string_view tr_dashboard_load_state(
    std::optional<double> cpu, std::optional<double> memory, std::optional<double> io,
    bool cache_busy, bool disk_busy, bool disk_critical)
{
    if (disk_critical || cpu.value_or(0) >= 20 || memory.value_or(0) >= 10 || io.value_or(0) >= 10)
        return "critical";
    if (cache_busy || disk_busy || cpu.value_or(0) >= 5 || memory.value_or(0) >= 1 || io.value_or(0) >= 1)
        return "busy";
    return cpu && memory && io ? "normal" : "unknown";
}
