const names = {
  http_request_rejected: 'HTTP-kérés elutasítva',
  p2p_message_rejected: 'Hibás P2P-üzenet elutasítva',
  rpc_access_denied: 'RPC-hozzáférés elutasítva',
  rpc_auth_failed: 'Sikertelen RPC-belépés',
  rpc_path_traversal_rejected: 'Veszélyes webes útvonal elutasítva',
  rpc_rate_limited: 'RPC-próbálkozás ideiglenesen tiltva',
  unsafe_file_path_rejected: 'Veszélyes fájlútvonal elutasítva',
};

export class SecurityPanel {
  constructor(remote, openLogs) {
    this.remote = remote;
    this.pending = false;
    this.root = document.createElement('article');
    this.root.className = 'dash-panel security-panel';
    this.root.innerHTML = `<div class="panel-heading"><div><p class="eyebrow">BIZTONSÁG</p><h2>Biztonsági események</h2></div><span class="security-icon" aria-hidden="true">⚠</span></div>
      <p class="security-summary" role="status" aria-live="polite">Események betöltése…</p>
      <ul class="security-recent"></ul>
      <p class="security-note">A megőrzött napló alapján. Egy korábbi elutasítás nem feltétlenül jelent aktív támadást.</p>
      <button type="button">Biztonsági napló megnyitása →</button>`;
    this.summary = this.root.querySelector('.security-summary');
    this.recent = this.root.querySelector('.security-recent');
    this.root.querySelector('button').addEventListener('click', openLogs);
  }

  markStale() {
    this.summary.textContent = this.root.classList.contains('has-incidents')
      ? 'Korábban biztonsági események voltak · az adatok most nem frissíthetők'
      : 'A biztonsági események állapota most nem ellenőrizhető';
  }

  update(result) {
    const external = result.vpn_entries?.entries;
    const rows = [
      ...result.entries.slice(-512),
      ...(Array.isArray(external) ? external.slice(-2000) : []),
    ]
      .filter(
        (row) =>
          row?.source === 'Security' &&
          typeof row.message === 'string' &&
          Number.isFinite(row.time_ms),
      )
      .toSorted((a, b) => b.time_ms - a.time_ms);
    this.root.classList.toggle('has-incidents', rows.length > 0);
    this.summary.textContent =
      rows.length > 0
        ? `⚠ ${rows.length.toLocaleString('hu-HU')} megőrzött biztonsági bejegyzés`
        : 'Nincs megőrzött biztonsági esemény.';
    const signature = JSON.stringify(rows.slice(0, 3));
    if (signature === this.signature) {
      return;
    }
    this.signature = signature;
    this.recent.replaceChildren(
      ...rows.slice(0, 3).map((row) => {
        const item = document.createElement('li');
        const code = /^event=([a-z0-9_]+)/.exec(row.message)?.[1];
        const label = names[code] ?? 'Biztonsági esemény';
        const count = /(?:^|\s)count=(\d+)/.exec(row.message)?.[1];
        const suffix = count ? ` · ${count} elutasítás` : '';
        item.textContent = `${new Date(row.time_ms).toLocaleString('hu-HU')} · ${label}${suffix}`;
        return item;
      }),
    );
  }

  async refresh() {
    if (this.pending) {
      return;
    }
    this.pending = true;
    let received = false;
    try {
      await this.remote.sendRequest(
        {
          id: 'security-overview',
          jsonrpc: '2.0',
          method: 'log_get',
          params: {},
        },
        (response) => {
          if (!response?.error && Array.isArray(response?.result?.entries)) {
            this.update(response.result);
            received = true;
          }
        },
        null,
        { quiet: true, timeout: 10_000 },
      );
    } catch {
      // Keep previously observed incidents visible when the server is offline.
    } finally {
      if (!received) {
        this.markStale();
      }
      this.pending = false;
    }
  }
}
