const states = {
  busy: ['Emelkedett terhelés', 'warn'],
  critical: ['Túlterhelés jelezve', 'bad'],
  normal: ['Nincs jelzett túlterhelés', 'good'],
  unknown: ['Nincs elegendő mérési adat', 'neutral'],
};
const percent = (value) => {
  return Number.isFinite(value)
    ? `${value.toLocaleString('hu-HU', { maximumFractionDigits: 2 })}%`
    : 'Nem elérhető';
};
const mib = (value) =>
  `${(value / 1024 / 1024).toLocaleString('hu-HU', { maximumFractionDigits: 1 })} MiB`;

export class StoragePanel {
  constructor() {
    this.root = document.createElement('article');
    this.root.className = 'dash-panel storage-panel';
    this.root.innerHTML = `<div class="panel-heading"><div><p class="eyebrow">TÁROLÓK ÉS RENDSZERTERHELÉS</p><h2>Könyvtárak és lemezprofilok</h2></div><span class="vpn-badge load-badge" data-tone="neutral" role="status">Mérésre vár</span></div>
      <div class="pressure-metrics"><div><span>CPU-várakozás</span><strong data-pressure="cpu">—</strong></div><div><span>Memóriavárakozás</span><strong data-pressure="memory">—</strong></div><div><span>I/O-várakozás</span><strong data-pressure="io">—</strong></div><div><span>Közös írási cache</span><strong data-pressure="cache">—</strong></div></div>
      <p class="storage-note">A tényleges I/O-könyvtárak jelennek meg, az ideiglenes letöltési helyet is beleértve.</p>
      <div class="storage-table-scroll"><table><thead><tr><th>Könyvtár</th><th>Alkalmazott profil</th><th>Torrentek</th><th>Lemezterhelés</th></tr></thead><tbody></tbody></table></div>
      <p class="storage-empty">Adatok betöltése…</p><p class="pressure-note">A várakozási arány 10 másodperces Linux PSI-átlag, nem CPU-kihasználtság. A lemezjelzés legfeljebb 2 perces mintákra támaszkodik.</p>
      <details class="chart-details"><summary>Mit jelent a terhelésjelzés?</summary><p>Emelkedett: CPU-várakozás ≥5%, memória- vagy I/O-várakozás ≥1%, terhelt lemez vagy telítődő cache. Túlterhelés: CPU-várakozás ≥20%, memória- vagy I/O-várakozás ≥10%, illetve kritikus lemez-válaszidő. Ezek tájékoztató küszöbök. Hiányzó rendszeradatnál nem jelzünk biztosan normál működést.</p></details>`;
  }

  markStale() {
    const badge = this.root.querySelector('.load-badge');
    badge.textContent = 'Nem ellenőrizhető · frissítés szükséges';
    badge.dataset.tone = 'neutral';
    for (const cell of this.root.querySelectorAll('.disk-load')) {
      cell.textContent = 'Elavult mérés';
      cell.dataset.tone = 'neutral';
    }
  }

  update(status) {
    if (!status) {
      this.markStale();
      this.root.querySelector('.storage-empty').textContent =
        'A tárolóadatokhoz v8 vagy újabb daemon szükséges.';
      return;
    }
    const [label, tone] = states[status.system_state] ?? states.unknown;
    const badge = this.root.querySelector('.load-badge');
    badge.textContent = label;
    badge.dataset.tone = tone;
    for (const key of ['cpu', 'memory', 'io']) {
      this.root.querySelector(`[data-pressure="${key}"]`).textContent = percent(
        status[`${key}_wait_percent`],
      );
    }
    this.root.querySelector('[data-pressure="cache"]').textContent =
      status.automatic
        ? `${mib(status.cache_reserved_bytes)} / ${mib(status.cache_capacity_bytes)}`
        : 'Kézi írási mód';
    this.root.querySelector('[data-pressure="cache"]').title =
      `Kiírásra vár: ${mib(status.cache_pending_bytes)}${status.cache_congested ? ' · A cache terhelt' : ''}`;
    const rows = document.createDocumentFragment();
    for (const directory of status.directories) {
      const row = document.createElement('tr');
      const profile =
        {
          hdd: 'HDD · automatikus',
          manual: `Kézi · ${status.manual_batch_bytes / 1024} KiB írás`,
          ssd: 'SSD · automatikus',
          unknown: 'Ismeretlen · 64 KiB tartalék',
        }[directory.profile] ?? 'Ismeretlen';
      const [loadLabel, loadTone] = states[directory.load] ?? states.unknown;
      for (const [index, text] of [
        directory.path,
        profile,
        `${directory.active} aktív / ${directory.torrents} összes`,
        loadLabel,
      ].entries()) {
        const cell = document.createElement('td');
        cell.textContent = text;
        if (index === 0) {
          cell.className = 'storage-path';
        }
        if (index === 3) {
          cell.className = 'disk-load';
          cell.dataset.tone = loadTone;
        }
        row.append(cell);
      }
      rows.append(row);
    }
    this.root.querySelector('tbody').replaceChildren(rows);
    this.root.querySelector('.storage-empty').hidden =
      status.directories.length > 0;
    this.root.querySelector('.storage-empty').textContent =
      'Még nincs torrenthez tartozó könyvtár. A profilok a torrentek hozzáadása után jelennek meg.';
  }
}
