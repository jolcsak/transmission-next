// SPDX-License-Identifier: GPL-2.0-or-later
#include "libtransmission/disk-profile.h"
#include <cctype>
#include <cerrno>
#include <filesystem>
#include <fstream>
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
} // namespace
tr_disk_profile tr_detect_disk_profile(std::string_view path, std::string_view sys_block_root)
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
    auto const device = std::to_string(major(info.st_dev)) + ':' + std::to_string(minor(info.st_dev));
    fallback.devices = { "fs:" + device };
    auto profile = tr_disk_profile{ .kind = tr_disk_profile::Kind::Ssd };
    if (collect_devices(fs::path{ sys_block_root } / device, profile, 0U) && !profile.devices.empty())
    {
        std::ranges::sort(profile.devices);
        profile.devices.erase(std::unique(profile.devices.begin(), profile.devices.end()), profile.devices.end());
        return profile;
    }
#else
    (void)path;
    (void)sys_block_root;
#endif
    return fallback;
}
