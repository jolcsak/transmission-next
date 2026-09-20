# Router-friendly daemon profile

Use these settings with the patched daemon to reduce outbound connection churn
and the connected peer population. No upload or download bandwidth cap is set.

- `peer_connection_attempts_per_second`: normal outgoing peer candidates admitted
  per second. Default 18; this profile uses 4, spread across 500 ms pulses. Odd
  values carry half-attempt credit between pulses. Idle time cannot accumulate
  a large burst. Values above 18 are capped at 18; 0 disables normal outgoing
  candidate attempts. This setting is loaded from settings.json, not exposed
  through a new RPC field.
- `peer_limit_global`: 80; `peer_limit_per_torrent`: 30. These use Transmission's
  existing peer-limit enforcement, not a hard limit on all router NAT entries.
- LPD and paused-torrent scrapes are disabled. DHT, PEX, uTP and port forwarding
  remain available.

Slower discovery can delay startup and recovery after peer loss. A smaller peer
population can reduce throughput in swarms that need many slow peers. The
profile does not impose a byte-per-second limit on established connections.

The pacing applies to normal outgoing candidate selection. Incoming connections,
kernel TCP retransmissions, transport fallback, DHT traffic and time-sensitive
BEP 55 holepunch callbacks are not globally packet-rate limited by this setting.

For an existing installation, stop the daemon and merge the desired keys into
its settings.json, preserving its other settings. Use the matching patched
binary; unmodified Transmission does not implement the new pacing key.

In the Linux package, run `bash run-daemon.sh` from the extracted directory.
It uses the adjacent `config/` and `downloads/` directories. RPC listens on
localhost port 9091. Stop with Ctrl+C. This package does not include a web UI.
