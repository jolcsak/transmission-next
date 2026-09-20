#!/usr/bin/env bash
set -euo pipefail
test "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)"
test "$(readlink /proc/self/ns/net)" != "$(readlink /proc/1/ns/net)"
test "$#" -ge 2
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mount --make-rprivate /
mount -t tmpfs tmpfs /run
mkdir -p /etc/netns
mount -t tmpfs tmpfs /etc/netns
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$root/integration_check.py" "$root" "$1" "$2" "${3:-udp}"
