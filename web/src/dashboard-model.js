// UTC calendar periods match the daemon's fixed-size transfer buckets.
export function summarizePeriod(history, period) {
  const now = history.now * 1000;
  const date = new Date(now);
  let start = Date.UTC(
    date.getUTCFullYear(),
    date.getUTCMonth(),
    date.getUTCDate(),
  );
  let step = 86_400;
  let count = 0;
  if (period === 'day') {
    count = 24;
    step = 3600;
  } else if (period === 'week') {
    start -= ((date.getUTCDay() + 6) % 7) * 86_400_000;
    count = 7;
  } else {
    start = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
    count = new Date(
      Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0),
    ).getUTCDate();
  }
  start /= 1000;
  const source = new Map(
    (period === 'day' ? history.hours : history.days).map((row) => [
      row[0],
      row,
    ]),
  );
  const points = Array.from({ length: count }, (_, index) => {
    const time = start + index * step;
    const row = source.get(time) ?? [time, 0, 0];
    return {
      available: time + step > history.started_at && time <= history.now,
      down: row[1],
      time,
      up: row[2],
    };
  });
  return {
    down: points.reduce((sum, point) => sum + point.down, 0),
    partial: history.started_at > start,
    points,
    start,
    up: points.reduce((sum, point) => sum + point.up, 0),
  };
}

export function vpnPresentation(vpn = {}) {
  const states = {
    connected: ['Kapcsolódva', 'good'],
    connecting: ['Kapcsolódás…', 'warn'],
    failed: ['Kapcsolati hiba', 'bad'],
    reconnecting: ['Újracsatlakozás…', 'warn'],
    starting: ['Indítás…', 'warn'],
    stopped: ['Leállítva', 'warn'],
    unknown: ['Nem ellenőrizhető', 'neutral'],
    unmanaged: ['Nem kezelt VPN', 'neutral'],
  };
  const [label, tone] = states[vpn.state] ?? states.unknown;
  return { label, tone };
}
