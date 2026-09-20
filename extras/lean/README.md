# Lean daemon profile

`lean` builds the daemon and RPC service. `lean-web` also installs the bundled web interface. Both keep DHT, PEX, µTP, encryption and automatic port forwarding. GTK, Qt, macOS GUI, utilities, translations, developer tests and man pages are excluded from these distribution builds. Use `-DENABLE_TESTS=ON` for development verification.

Requires CMake 3.21+, Ninja, a supported C++20 compiler, and the normal Transmission development dependencies. The preset uses bundled fmt to avoid accidentally picking up Windows headers from a WSL PATH. It does not change the supported cryptography backend selection.

From the source root:

```sh
cmake --preset lean
cmake --build --preset lean --parallel 6
# Or build a Linux distribution archive in a fresh directory:
bash extras/lean/package.sh lean /absolute/output/path
# Optional web UI:
bash extras/lean/package.sh lean-web /absolute/other/output/path
```

In an extracted package, run `bash run-daemon.sh`. Configuration lives in `config/` and downloads in `downloads/` next to the launcher. RPC listens on localhost port 9091. A web package serves its UI at `http://127.0.0.1:9091/transmission/web/`. Use another port with `bash run-daemon.sh --port 19091` if needed. Stop with Ctrl+C.

The supplied settings disable local peer discovery (LPD), scraping paused torrents, directory watching, blocklists, script hooks and the alternative-speed scheduler. Most of these latter features were already disabled by default. LPD and paused scraping are runtime settings: their implementation remains in the binary. This profile therefore primarily reduces the distribution contents and optional background work, not the core download algorithm.

Existing configuration is not migrated or overwritten. To use this profile with an existing installation, merge only the desired settings while its daemon is stopped. Build presets alone do not apply runtime settings; the package includes the settings file and launcher for that purpose.

The Linux archive is dynamically linked. A compatible Linux distribution needs the runtime libraries listed by `ldd bin/transmission-daemon`. Windows and macOS executables are not included. Tests and performance measurements remain in the source tree.
