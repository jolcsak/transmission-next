// SPDX-License-Identifier: GPL-2.0-or-later
#include "libtransmission/disk-profile.h"
#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <system_error>
#ifdef __linux__
#include <sys/stat.h>
#include <sys/sysmacros.h>
#endif
namespace
{
namespace fs = std::filesystem;
std::string read_line(fs::path const& path)
{
    auto line = std::string{};
    auto stream = std::ifstream{ path };
    std::getline(stream, line);
    return line;
}

std::string mount_unescape(std::string_view text)
{
    auto result = std::string{};
    result.reserve(text.size());
    for (size_t i = 0; i < text.size(); ++i)
    {
        if (text[i] == '\\' && i + 3U < text.size() && text[i + 1U] >= '0' && text[i + 1U] <= '7' &&
            text[i + 2U] >= '0' && text[i + 2U] <= '7' && text[i + 3U] >= '0' && text[i + 3U] <= '7')
        {
            result.push_back(static_cast<char>((text[i + 1U] - '0') * 64 + (text[i + 2U] - '0') * 8 + text[i + 3U] - '0'));
            i += 3U;
        }
        else
        {
            result.push_back(text[i]);
        }
    }
    return result;
}

bool path_contains(fs::path const& parent, fs::path const& child)
{
    auto left = parent.begin();
    auto right = child.begin();
    for (; left != parent.end() && right != child.end(); ++left, ++right)
    {
        if (*left != *right)
        {
            return false;
        }
    }
    return left == parent.end();
}

std::optional<tr_disk_profile> explicit_profile(fs::path const& path, std::string_view rules)
{
    auto best = size_t{};
    auto result = std::optional<tr_disk_profile>{};
    for (size_t begin = 0; begin <= rules.size();)
    {
        auto const end = rules.find(';', begin);
        auto const item = rules.substr(begin, end == std::string_view::npos ? rules.size() - begin : end - begin);
        auto const separator = item.rfind('=');
        if (separator != std::string_view::npos)
        {
            auto const configured = item.substr(0, separator);
            auto const kind = item.substr(separator + 1U);
            auto const candidate = configured == "*" ? fs::path{} : fs::path{ configured };
            auto const matches = configured == "*" || (candidate.is_absolute() && path_contains(candidate.lexically_normal(), path));
            auto const specificity = configured == "*" ? 1U : configured.size() + 1U;
            if (matches && specificity > best && (kind == "hdd" || kind == "ssd"))
            {
                result = tr_disk_profile{
                    .kind = kind == "hdd" ? tr_disk_profile::Kind::Hdd : tr_disk_profile::Kind::Ssd,
                    .devices = { "configured:" + std::string{ configured } },
                };
                best = specificity;
            }
        }
        if (end == std::string_view::npos)
        {
            break;
        }
        begin = end + 1U;
    }
    return result;
}

bool collect_devices(fs::path path, tr_disk_profile& profile, unsigned depth)
{
    if (depth > 8U)
    {
        return false;
    }
    auto error = std::error_code{};
    path = fs::canonical(path, error);
    if (error)
    {
        return false;
    }
    if (fs::exists(path / "partition", error))
    {
        path = path.parent_path();
    }
    auto const slaves = path / "slaves";
    if (fs::exists(slaves, error))
    {
        auto found = false;
        auto iter = fs::directory_iterator{ slaves, error };
        auto const end = fs::directory_iterator{};
        for (; !error && iter != end; iter.increment(error))
        {
            found = true;
            if (!collect_devices(iter->path(), profile, depth + 1U))
            {
                return false;
            }
        }
        if (error || found)
        {
            return !error;
        }
    }
    else if (error)
    {
        return false;
    }
    auto model = read_line(path / "device/model");
    std::ranges::transform(model, model.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    auto const name = path.generic_string();
    if (name.find("/virtual/") != std::string::npos || name.find("/virtio") != std::string::npos ||
        model.find("virtual") != std::string::npos || model.find("qemu") != std::string::npos ||
        model.find("vmware") != std::string::npos)
    {
        return false;
    }
    auto const rotational = read_line(path / "queue/rotational");
    if (rotational != "0" && rotational != "1")
    {
        return false;
    }
    if (rotational == "1")
    {
        profile.kind = tr_disk_profile::Kind::Hdd;
    }
    profile.devices.push_back(name);
    return true;
}

std::optional<fs::path> mount_source(fs::path const& path, fs::path const& mountinfo_path)
{
    auto input = std::ifstream{ mountinfo_path };
    auto best = size_t{};
    auto source = std::optional<fs::path>{};
    for (auto line = std::string{}; std::getline(input, line);)
    {
        auto const separator = line.find(" - ");
        if (separator == std::string::npos)
        {
            continue;
        }
        auto before = std::istringstream{ line.substr(0, separator) };
        auto fields = std::vector<std::string>{};
        for (auto field = std::string{}; before >> field;)
        {
            fields.emplace_back(std::move(field));
        }
        auto after = std::istringstream{ line.substr(separator + 3U) };
        [[maybe_unused]] auto filesystem = std::string{};
        auto raw_source = std::string{};
        after >> filesystem >> raw_source;
        if (fields.size() < 5U || !raw_source.starts_with("/dev/"))
        {
            continue;
        }
        auto const mountpoint = fs::path{ mount_unescape(fields[4]) }.lexically_normal();
        if (path_contains(mountpoint, path) && mountpoint.native().size() >= best)
        {
            best = mountpoint.native().size();
            source = fs::path{ mount_unescape(raw_source) };
        }
    }
    return source;
}

std::optional<fs::path> sysfs_source(fs::path const& source, fs::path const& class_root)
{
    auto error = std::error_code{};
    auto const name = source.filename();
    if (fs::exists(class_root / name, error))
    {
        return class_root / name;
    }
    // /dev/mapper names do not match dm-N. Match the stable device-mapper name.
    auto iter = fs::directory_iterator{ class_root, error };
    auto const end = fs::directory_iterator{};
    for (; !error && iter != end; iter.increment(error))
    {
        if (read_line(iter->path() / "dm/name") == name.string())
        {
            return iter->path();
        }
    }
    return std::nullopt;
}
} // namespace
tr_disk_profile tr_detect_disk_profile(
    std::string_view path,
    std::string_view sys_block_root,
    std::string_view mountinfo_path,
    std::string_view sys_class_root,
    std::string_view rules)
{
    auto fallback = tr_disk_profile{ .devices = { "unknown" } };
#ifdef __linux__
    if (path.empty())
    {
        return fallback;
    }
    auto error = std::error_code{};
    auto existing = fs::absolute(fs::path{ path }, error);
    if (error)
    {
        return fallback;
    }
    existing = fs::weakly_canonical(existing, error);
    if (error)
    {
        return fallback;
    }
    struct stat info{};
    while (::stat(existing.c_str(), &info) != 0)
    {
        if ((errno != ENOENT && errno != ENOTDIR) || existing == existing.root_path())
        {
            return fallback;
        }
        existing = existing.parent_path();
    }
    if (rules.empty())
    {
        if (auto const* const configured = std::getenv("TRANSMISSION_DISK_PROFILE_RULES"); configured != nullptr)
        {
            rules = configured;
        }
    }
    if (auto profile = explicit_profile(existing, rules); profile)
    {
        return *profile;
    }
    auto const device = std::to_string(major(info.st_dev)) + ':' + std::to_string(minor(info.st_dev));
    fallback.devices = { "fs:" + device };
    auto profile = tr_disk_profile{ .kind = tr_disk_profile::Kind::Ssd, .devices = {} };
    if (collect_devices(fs::path{ sys_block_root } / device, profile, 0U) && !profile.devices.empty())
    {
        std::ranges::sort(profile.devices);
        profile.devices.erase(std::unique(profile.devices.begin(), profile.devices.end()), profile.devices.end());
        return profile;
    }
    profile = tr_disk_profile{ .kind = tr_disk_profile::Kind::Ssd, .devices = {} };
    if (auto const source = mount_source(existing, fs::path{ mountinfo_path }); source)
    {
        if (auto const sysfs = sysfs_source(*source, fs::path{ sys_class_root }); sysfs &&
            collect_devices(*sysfs, profile, 0U) && !profile.devices.empty())
        {
            std::ranges::sort(profile.devices);
            profile.devices.erase(std::unique(profile.devices.begin(), profile.devices.end()), profile.devices.end());
            return profile;
        }
    }
#else
    (void)path;
    (void)sys_block_root;
#endif
    return fallback;
}
