#!/usr/bin/env bash
# Usage: bash extras/lean/package.sh lean|lean-web /absolute/output/directory
set -euo pipefail
profile=${1:-lean}
case "$profile" in lean|lean-web) ;; *) printf 'Unknown profile: %s\n' "$profile" >&2; exit 2;; esac
out=$(realpath -m "${2:?Output directory required}")
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
package="$out/transmission-$profile"
if [[ -e "$package" ]]; then
    printf 'Output already exists: %s\n' "$package" >&2
    exit 1
fi
cd "$source_dir"
cmake --preset "$profile"
cmake --build --preset "$profile" --parallel "${JOBS:-6}"
cmake --install "build/$profile" --prefix "$package" --strip
mkdir -p "$package/config"
cp extras/lean/settings.json "$package/config/settings.json"
cp extras/lean/run-daemon.sh "$package/run-daemon.sh"
cp extras/lean/README.md "$package/README.md"
cp COPYING "$package/COPYING"
cp -R licenses "$package/licenses"
chmod +x "$package/run-daemon.sh"
tar -C "$out" -czf "$package-linux-$(uname -m).tar.gz" "transmission-$profile"
printf 'Package: %s-linux-%s.tar.gz\n' "$package" "$(uname -m)"
