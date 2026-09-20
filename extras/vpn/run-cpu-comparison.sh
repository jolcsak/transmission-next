#!/usr/bin/env bash
# sudo bash run-cpu-comparison.sh OLD/extras/vpn NEW/extras/vpn DAEMON FRESH_OUTPUT
set -euo pipefail
test "$#" -eq 4
baseline=$(realpath "$1")
optimized=$(realpath "$2")
daemon=$(realpath "$3")
output=$(realpath -m "$4")
mkdir "$output"
for variant in a1 b1 b2 a2; do
    source="$optimized"
    [[ "$variant" != a* ]] || source="$baseline"
    env VPN_BENCH_TRANSFER=1 PYTHONDONTWRITEBYTECODE=1 unshare --mount --net bash -c '
        set -euo pipefail
        test "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)"
        test "$(readlink /proc/self/ns/net)" != "$(readlink /proc/1/ns/net)"
        mount --make-rprivate /
        mount -t tmpfs tmpfs /run
        mkdir -p /etc/netns
        mount -t tmpfs tmpfs /etc/netns
        exec python3 "$1/integration_check.py" "$2" "$3" "$4" udp
    ' _ "$optimized" "$source" "$daemon" "$output/$variant" > "$output/$variant-console.log"
done
echo "ABBA results: $output/{a1,b1,b2,a2}/results.json"
