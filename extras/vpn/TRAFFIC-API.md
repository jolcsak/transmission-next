# VPN traffic

Capability: `vpn.traffic.v1`.

`telemetry_get` and `session_stats` with `include_history: true` include these
optional fields in `vpn_status` when managed tunnel `tun0` exists:

- `received_bytes`, `sent_bytes`: tunnel IP traffic, including peer, webseed,
  tracker, DNS and other traffic inside this namespace.
- `received_packets`, `sent_packets`: tunnel packet counts.
- `traffic_scope`: `current_tunnel`.
- `traffic_sampled_at`: Unix timestamp (seconds).

These are kernel counters since the current interface was created. They can
reset on reconnect/interface recreation and are not daily/monthly/lifetime
totals. They exclude outer OpenVPN encryption/UDP overhead. No per-torrent
attribution is implied. Missing tunnel/counters means unavailable, not zero.
Kernel reads add no network probes or periodic disk writes.

The dashboard shows bytes and packets separately from torrent payload totals.
MQTT state JSON carries the same fields. InfluxDB's `transmission` measurement
exports them with a `vpn_` prefix. Export remains optional, using its existing
interval and batch settings. Consumers calculating deltas must handle resets.
