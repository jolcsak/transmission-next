# HDD profile

This optional profile retains the SSD changes: unchanged state files are not
rewritten, statistics use 30-minute checkpoints plus normal shutdown saves,
partial pieces are preferred within equal priority and rarity, and new downloads
write directly into the destination directory. Full zero-filling is not enabled.

`disk_write_batch_size_kib: 256` permits adjacent payload blocks from one read
callback to be combined into writes up to 256 KiB. File boundaries and gaps still
split writes. The default is 64 KiB, preserving the SSD profile. Effective values
are bounded to 64–256 KiB. Use the patched binary; this is a settings-file key,
not a newly exposed RPC field. It is session-wide, not automatic drive detection.

The larger buffer is allocated only when a complete payload block arrives and is
released at the end of that callback. Allocation failure falls back to the 64 KiB
buffer. Nothing is retained for a later timer; completed-block notifications still
follow successful writes. This does not add an application writeback cache.

One download slot and two seed slots reduce concurrent file activity. Stalled
torrents still occupy a slot; a stalled download can therefore hold up the queue.
Manual force-start can bypass the queue. The profile does not cap transfer speeds,
and one torrent can still access many files. Seeding concurrency can affect swarm
availability and aggregate upload speed.

Keep the SSD profile for SSD-only or mixed-drive sessions unless measurements
favor these settings. Separate daemon instances and configuration directories can
be used for separate disks. Never run both instances with the same config or
actively downloading into the same payload files.

For an existing installation, stop the daemon and merge selected fields into its
settings, preserving download paths and RPC authentication. No existing files are
moved. More sequential application writes are not proof of fewer physical seeks:
the filesystem and kernel can split, merge, and reorder them.
