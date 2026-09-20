#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cc -std=c11 -O2 -Wall -Wextra -Werror -Wl,-z,relro,-z,now \
  "$root/rpc-relay.c" -o "$root/rpc-relay"
