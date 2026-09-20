# SSD profile

`incomplete_dir_enabled: false` writes new downloads directly into their selected
destination directory. Completion does not require moving payload data from a
separate filesystem. Existing download paths are not rewritten, and existing
partial downloads must be handled separately before changing their location.

Cumulative statistics are checkpointed at most once every 30 minutes while dirty,
and saved on normal shutdown. A crash can lose up to roughly 30 minutes of these
counters; torrent resume checkpoints retain their existing interval.

This profile retains the router limits and queues downloads with two active slots.
Transmission's existing stalled-torrent rules can let stalled torrents stop counting
against that queue limit. Upload speed and download speed are not capped.

`preallocation: 1` selects fast/sparse preallocation. It allows native filesystem
allocation and avoids the full-preallocation fallback that writes zeros throughout
the file. It does not guarantee a physically sparse file on every platform.

`message_level: 2` keeps warning and error messages, suppressing routine info/debug
messages. If diagnosing a problem, temporarily increase the log level.

Use this as an optional profile for a new configuration directory, or merge the
listed fields into a stopped daemon's settings. Do not replace an existing settings
file without preserving its download paths, RPC authentication and other preferences.

The application uses its existing bounded write batches and the operating system's
page cache. This profile adds no delayed application writeback cache and does not
extend the interval during which received blocks exist only in application RAM.
