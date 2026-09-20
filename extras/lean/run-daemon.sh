#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -d "$root/share/transmission/public_html" ]]; then
    export TRANSMISSION_WEB_HOME="$root/share/transmission/public_html"
fi
cd "$root"
exec "$root/bin/transmission-daemon" --foreground --config-dir "$root/config" --download-dir "$root/downloads" "$@"
