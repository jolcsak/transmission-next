# Automatic disk profile (Linux)

One daemon chooses a policy for each torrent's current data directory. Enable with
`auto_disk_profile_enabled: true`. The shipped auto configuration enables it;
manual HDD and SSD profiles remain available and unchanged.

The detector resolves the directory (or its nearest existing ancestor), gets the
filesystem device, and reads Linux sysfs `queue/rotational`. Partitions are grouped
by their parent disk. Device-mapper/RAID slave devices are followed with bounded
recursion; shared backing disks count towards the same admission limits.

| Detected storage | Write batch | Download admissions per device | Seed admissions |
|---|---:|---:|---:|
| HDD | up to 512 KiB | starts at 2, grows to 4 | 2 |
| SSD/non-rotational | up to 512 KiB | starts at 4, grows to 8 | unrestricted by disk policy |
| Unknown | up to 512 KiB | 2 per filesystem when identifiable | unrestricted by disk policy |

Auto mode now has a shared 32 MiB payload cache, allocated on demand in 512 KiB
regions, plus bounded per-block bookkeeping. It combines blocks across network
callbacks and peers, including webseeds, in torrent/file order. Completed pieces
of at least 64 KiB that fit a region flush promptly. Other regions become eligible
after 250 ms and are drained by the existing 500 ms pulse. Each pulse handles at
most 8 regions and yields after 10 ms; an individual synchronous write cannot be
interrupted. Capacity pressure evicts the oldest region. One drained 512 KiB buffer
is reused for up to about two idle seconds, within the 32 MiB cap, to avoid repeated
allocation costs; other drained buffers are released. The OS can retain already-written
pages in its own cache.

BitTorrent cache completions retain the sender directly, avoiding a separately
allocated callback closure for each 16 KiB block. Region bookkeeping uses a
32-bit block mask and derives write lengths from torrent geometry. This saves
512 bytes of fixed bookkeeping per region on the Linux x86-64 build, plus the
removed BitTorrent callback allocations. The 32 MiB payload capacity, write
batch sizes and adaptive download admission policy are unchanged.

Healthy drain windows of 16 writes, at least 14 below one quarter of the busy
threshold, can raise admissions by one at intervals of at least 30 seconds.
There must be spare cache capacity. Slow writes or low free capacity reduce the
learned limit. At 75% cache reservation, or an observed backlog older than a
second, new download admissions are capped at 1 and per-peer requests at 8.
Disk-latency restrictions below can impose tighter limits.

Virtual disks, missing/unreadable sysfs attributes and unresolvable devices use
the unknown fallback. In particular WSL's Virtual Disk does not identify the
physical Windows drive. Network filesystems and unsupported OSes also fall back;
this release does not query Windows host hardware from inside WSL.

The current torrent directory is rechecked on location changes and approximately
once a minute through the existing queue timer. There is no sysfs probe for each
received block. Nested mounts or individual file symlinks inside a torrent are
not independently profiled; detection uses its current base directory.

Successful buffered payload reads and writes are timed separately, without probe
writes or new fsync calls. Each window needs 16 operations within 10 seconds;
at least 8 must exceed the threshold. HDD thresholds are 30 ms (busy) and 120 ms
(critical), SSD/unknown thresholds 20 ms and 80 ms. These are initial heuristics,
not hardware identification or measurements of physical device latency.

Busy devices admit up to 1 download and 2 seeds; critical devices admit no new
torrents until recovery. These admission restrictions cover shared backing disks.
Each affected torrent also limits new requests per peer to 8/2 blocks on HDD or
16/4 on SSD/unknown (busy/critical). Outstanding requests are allowed to finish.
Request caps currently follow that torrent's own I/O observations, not other
torrents' observations. Manual mode retains its 256/64 KiB callback batches.

Recovery requires a 30-second hold followed by two healthy windows, each with at
least 14 operations below one quarter of the busy threshold. It drops one level
at a time and starts another hold. Fast writes cannot erase slow-read pressure.
Partial windows expire after 10 seconds; pressure without a completed window
expires after 120 seconds, allowing idle disks another attempt. Kernel caching
can hide physical latency. No disk-speed probe is used.

Admission control does not stop existing transfers. Completion, relocation or a
manual force-start can temporarily exceed the limits. Stalled running torrents
still count towards the per-device limit. Existing explicit global queue limits
are honored as an additional restriction. This preset disables global queue
limits so independent disks do not block one another.

The detected type is logged when it changes at info level (`message_level: 3`).
Routine logging stays at level 2 in this profile. No extra probe files, device
writes, mount changes or kernel tuning are performed by the released daemon.

State-save deduplication, 30-minute statistics checkpoints, rare-piece priorities
and network limits remain. Cached blocks are not marked complete, hashed or
advertised until file writes succeed. Pending cache data is not saved as complete
in resume files and must be downloaded again after a crash. Successful buffered
writes still do not guarantee power-loss durability; no new fsync calls are added.
Stopping, verification, relocation, renaming and peer disconnect drain the cache.
Explicit payload deletion discards pending data to avoid recreating deleted files.
Write failures stop the torrent and discard uncommitted blocks. Already completed
blocks cannot be overwritten by older pending entries.

For manual control, disable auto mode and select the full manual HDD/SSD settings,
including queue settings. Do not merely disable auto mode in this preset if you
want global queues: this preset has them disabled. Preserve existing paths and
RPC credentials when merging settings into a stopped daemon's configuration.
