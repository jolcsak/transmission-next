// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <cstddef>
#include <chrono>
#include <limits>
#include <string>
#include <string_view>
#include <vector>

struct tr_disk_profile
{
    enum class Kind
    {
        Unknown,
        Ssd,
        Hdd
    };
    Kind kind = Kind::Unknown;
    std::vector<std::string> devices;
    [[nodiscard]] size_t write_batch_size() const noexcept
    {
        return (kind == Kind::Hdd ? 256U : 64U) * 1024U;
    }
    [[nodiscard]] size_t queue_limit(bool downloading) const noexcept
    {
        return downloading ? (kind == Kind::Ssd ? 4U : 2U) : (kind == Kind::Hdd ? 2U : std::numeric_limits<size_t>::max());
    }
    [[nodiscard]] bool shares_device(tr_disk_profile const& that) const noexcept
    {
        return std::ranges::any_of(
            devices,
            [&that](auto const& device) { return std::ranges::find(that.devices, device) != that.devices.end(); });
    }
};
// Alternate kernel metadata paths and explicit rules are exposed for tests.
// Production first applies TRANSMISSION_DISK_PROFILE_RULES, then resolves the
// filesystem device through sysfs and finally through Linux mountinfo. Rules use
// `/absolute/path=hdd;/other/path=ssd;*=hdd`; the longest matching path wins.
[[nodiscard]] tr_disk_profile tr_detect_disk_profile(
    std::string_view path,
    std::string_view sys_block_root = "/sys/dev/block",
    std::string_view mountinfo_path = "/proc/self/mountinfo",
    std::string_view sys_class_root = "/sys/class/block",
    std::string_view rules = {});

// Buffered I/O latency signals pressure, never hardware identity. No probe I/O.
class tr_disk_latency
{
public:
    using Clock = std::chrono::steady_clock;
    enum class Level
    {
        Normal,
        Busy,
        Critical
    };
    enum class Operation
    {
        Write,
        Read
    };
    void observe(
        Clock::duration elapsed,
        Clock::time_point now,
        tr_disk_profile::Kind kind = tr_disk_profile::Kind::Unknown,
        Operation operation = Operation::Write)
    {
        auto& window = windows_[operation == Operation::Read ? 1 : 0];
        if (window.samples != 0U && now - window.started > std::chrono::seconds{ 10 })
        {
            window.samples = window.slow = window.severe = window.fast = window.healthy = 0U;
        }
        if (window.samples == 0U)
            window.started = now;
        auto const busy = std::chrono::milliseconds{ kind == tr_disk_profile::Kind::Hdd ? 30 : 20 };
        auto const critical = busy * 4;
        ++window.samples;
        window.slow += elapsed >= busy;
        window.severe += elapsed >= critical;
        window.fast += elapsed < busy / 4;
        if (window.samples < 16U)
            return;
        auto const observed = window.severe >= 8U ? Level::Critical : (window.slow >= 8U ? Level::Busy : Level::Normal);
        if (now - window.updated >= std::chrono::seconds{ 120 })
            window.level = Level::Normal;
        if (observed != Level::Normal)
        {
            window.level = std::max(window.level, observed);
            window.hold_until = now + std::chrono::seconds{ 30 };
            window.healthy = 0U;
        }
        else if (window.fast >= 14U && now >= window.hold_until)
        {
            if (++window.healthy >= 2U)
            {
                window.level = window.level == Level::Critical ? Level::Busy : Level::Normal;
                window.hold_until = now + std::chrono::seconds{ 30 };
                window.healthy = 0U;
            }
        }
        else
            window.healthy = 0U;
        window.updated = now;
        window.samples = window.slow = window.severe = window.fast = 0U;
    }
    [[nodiscard]] Level level(Clock::time_point now = Clock::now()) const noexcept
    {
        auto result = Level::Normal;
        for (auto const& window : windows_)
            if (now - window.updated < std::chrono::seconds{ 120 })
                result = std::max(result, window.level);
        return result;
    }
    [[nodiscard]] bool pressured() const noexcept
    {
        return level() != Level::Normal;
    }
    [[nodiscard]] bool has_recent_sample(Clock::time_point now = Clock::now()) const noexcept
    {
        return std::ranges::any_of(windows_, [now](auto const& window)
        {
            return window.updated != Clock::time_point{} && now - window.updated < std::chrono::seconds{ 120 };
        });
    }
    [[nodiscard]] size_t request_limit(tr_disk_profile::Kind kind) const noexcept
    {
        switch (level())
        {
        case Level::Critical:
            return kind == tr_disk_profile::Kind::Hdd ? 2U : 4U;
        case Level::Busy:
            return kind == tr_disk_profile::Kind::Hdd ? 8U : 16U;
        default:
            return std::numeric_limits<size_t>::max();
        }
    }

private:
    struct Window
    {
        size_t samples = 0U, slow = 0U, severe = 0U, fast = 0U, healthy = 0U;
        Level level = Level::Normal;
        Clock::time_point started{}, updated{}, hold_until{};
    };
    Window windows_[2];
};
