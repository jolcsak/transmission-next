import { Formatter } from './formatter.js';
import { RPC } from './remote.js';
import { VisibleInterval } from './visible-interval.js';
import { StoragePanel } from './storage-panel.js';
import { TelemetrySettings } from './telemetry-settings.js';
import { RpcSecuritySettings } from './rpc-security-settings.js';
import { AccountMenu } from './account-menu.js';
import { VpnSettings } from './vpn-settings.js';
import { LogPanel } from './log-panel.js';
import { SecurityPanel } from './security-panel.js';
import { summarizePeriod, vpnPresentation } from './dashboard-model.js';

const size = (value) => (value === 0 ? '0 B' : Formatter.size(value));
const dateLabel = (time, options) =>
  new Date(time * 1000).toLocaleString('hu-HU', {
    timeZone: 'UTC',
    ...options,
  });

export class Dashboard {
  constructor(transmission) {
    this.transmission = transmission;
    this.remote = transmission.remote;
    this.period = 'week';
    this.root = document.createElement('section');
    this.root.id = 'dashboard';
    this.root.lang = 'hu';
    this.root.setAttribute('aria-label', 'Statisztikák, beállítások és napló');
    this.remote.onConnectionState((state) => this.setConnectionState(state));
    this.root.innerHTML = `
      <div class="dash-heading"><div><p class="eyebrow">A FORGALMAD, ÁTTEKINTHETŐEN</p><h1>Minden egy helyen.</h1><p class="dash-subtitle">Átvitelek, előzmények és a kapcsolatod állapota.</p></div><span class="dash-sync" role="status">Kapcsolódás a daemonhoz…</span></div>
      <div class="dash-metrics">
        <article class="dash-card"><span class="metric-icon down">↓</span><p>Letöltési sebesség</p><strong data-value="download">—</strong><small>Aktuális adatforgalom</small></article>
        <article class="dash-card"><span class="metric-icon up">↑</span><p>Feltöltési sebesség</p><strong data-value="upload">—</strong><small>Aktuális adatforgalom</small></article>
        <article class="dash-card"><span class="metric-icon">⇄</span><p>Aktív torrentek</p><strong data-value="active">—</strong><small data-value="total">Torrentek betöltése…</small></article>
        <article class="dash-card"><span class="metric-icon">◷</span><p>Futási idő</p><strong data-value="uptime">—</strong><small>A daemon indítása óta</small></article>
      </div>
      <div class="dash-columns">
        <article class="dash-panel transfer-panel">
          <div class="panel-heading"><div><p class="eyebrow">ÁTVITELI ELŐZMÉNYEK</p><h2>Forgalom időszakonként</h2></div><div class="period-switch" role="group" aria-label="Időszak"><button type="button" data-period="day" aria-pressed="false">Nap</button><button type="button" data-period="week" aria-pressed="true">Hét</button><button type="button" data-period="month" aria-pressed="false">Hónap</button></div></div>
          <p class="period-caption">Előzmények betöltése…</p>
          <div class="period-totals"><div><span><i class="legend-dot down"></i> Letöltés</span><strong data-value="period-down">—</strong></div><div><span><i class="legend-dot up"></i> Feltöltés</span><strong data-value="period-up">—</strong></div><div><span>Megosztási arány</span><strong data-value="ratio">—</strong></div></div>
          <div class="chart-scale"></div><div class="transfer-chart" aria-label="Letöltött és feltöltött adatmennyiség"></div><div class="chart-labels"></div>
          <p class="history-note">Az előzményt a daemon gyűjti, bezárt böngésző mellett is.</p>
          <details class="chart-details"><summary>Adatok táblázatban</summary><div class="table-scroll"><table><thead><tr><th>Időszak (UTC)</th><th>Letöltés</th><th>Feltöltés</th></tr></thead><tbody></tbody></table></div></details>
        </article>
        <aside class="dash-side">
          <article class="dash-panel vpn-panel"><div class="panel-heading"><div><p class="eyebrow">KAPCSOLAT</p><h2>VPN-állapot</h2></div><span class="shield-icon" aria-hidden="true">◇</span></div><div class="vpn-badge" data-tone="neutral" role="status">Nem ellenőrizhető</div><dl><div><dt>Szolgáltató</dt><dd data-value="vpn-provider">—</dd></div><div><dt>VPN-szerver</dt><dd data-value="vpn-server">—</dd></div><div><dt>Feloldott végpont</dt><dd data-value="vpn-endpoint">—</dd></div><div><dt>Tunnel IPv4</dt><dd data-value="vpn-tunnel-ip">—</dd></div><div><dt>Protokoll</dt><dd data-value="vpn-protocol">—</dd></div><div><dt>VPN ↓ fogadott adat</dt><dd data-value="vpn-received">—</dd></div><div><dt>VPN ↑ küldött adat</dt><dd data-value="vpn-sent">—</dd></div><div><dt>VPN csomagok ↓ / ↑</dt><dd data-value="vpn-packets">—</dd></div><div><dt>Forgalomvédelem</dt><dd data-value="vpn-guard">—</dd></div><div><dt>Állapotváltás</dt><dd data-value="vpn-time">—</dd></div></dl><p class="vpn-note">A VPN-felügyelő állapota. Külső IP-cím szolgáltatást nem kérdezünk le.</p></article>
          <article class="dash-panel lifetime-panel"><p class="eyebrow">ÖSSZESÍTETT FORGALOM</p><h2>Az összes munkamenet</h2><dl><div><dt>↓ Letöltve</dt><dd data-value="lifetime-down">—</dd></div><div><dt>↑ Feltöltve</dt><dd data-value="lifetime-up">—</dd></div></dl><p>A korábban gyűjtött összesített adatokkal együtt.</p></article>
        </aside>
      </div>
      <div class="dash-footer"><span>10 másodpercenként frissül · háttérben szünetel</span><button type="button" class="open-transfers">Torrentek kezelése →</button></div>`;
    const main = document.querySelector('#mainwin');
    this.security = new SecurityPanel(this.remote, () => {
      this.logs.search.value = 'Security';
      this.logs.level.value = '0';
      this.logs.sort.value = 'newest';
      this.logs.render();
      this.show('logs');
    });
    this.root.querySelector('.dash-columns').before(this.security.root);
    this.storage = new StoragePanel();
    this.root.querySelector('.dash-footer').before(this.storage.root);
    const overview = document.createElement('section');
    overview.id = 'statistics-page';
    overview.setAttribute('aria-label', 'Statisztikák');
    overview.append(...this.root.childNodes);
    overview.querySelector('h1').textContent = 'Statisztikák';
    const settings = document.createElement('section');
    settings.id = 'settings-page';
    settings.setAttribute('aria-label', 'Beállítások');
    settings.innerHTML =
      '<div class="dash-heading"><div><h1>Beállítások</h1><p>RPC-hitelesítés, VPN, MQTT és InfluxDB konfiguráció, tesztelés, import és export.</p></div></div>';
    const logs = document.createElement('section');
    logs.id = 'logs-page';
    logs.setAttribute('aria-label', 'Napló');
    logs.innerHTML =
      '<div class="dash-heading"><div><h1>Napló</h1><p>Transmission-üzenetek keresése, szűrése és rendezése.</p></div></div>';
    this.pages = { logs, overview, settings };
    this.root.append(overview, settings, logs);
    this.rpcSecurity = new RpcSecuritySettings(this.remote, (account) =>
      this.account?.render(account),
    );
    settings.append(this.rpcSecurity.root);
    this.settings = new TelemetrySettings(this.remote);
    settings.append(this.settings.root);
    this.vpnSettings = new VpnSettings(this.remote);
    settings.append(this.vpnSettings.root);
    this.logs = new LogPanel(this.remote);
    logs.append(this.logs.root);
    this.navigation = document.createElement('header');
    this.navigation.className = 'app-navigation';
    this.navigation.lang = 'hu';
    this.navigation.innerHTML = `<div class="app-brand"><span class="brand-mark" aria-hidden="true">T</span><div>Transmission<small>TRANSFER MANAGER</small></div></div><nav aria-label="Fő navigáció"><button type="button" data-view="overview" aria-controls="statistics-page" aria-current="page">Statisztikák</button><button type="button" data-view="settings" aria-controls="settings-page">Beállítások</button><button type="button" data-view="logs" aria-controls="logs-page">Napló</button><button type="button" data-view="torrents" aria-controls="mainwin-workarea">Torrentek</button></nav><span class="app-edition">LOCAL / v23</span>`;
    this.account = new AccountMenu(this.remote, {
      onChangePassword: () => {
        this.show('settings');
        this.rpcSecurity.root.open = true;
        this.rpcSecurity.form.elements.password.focus();
      },
    });
    this.navigation.append(this.account.root);
    main.prepend(this.navigation, this.root);
    this.transferElements = [
      '#mainwin-toolbar',
      '#mainwin-statusbar',
      '#mainwin-workarea',
    ].map((selector) => document.querySelector(selector));
    for (const button of this.navigation.querySelectorAll('[data-view]')) {
      button.addEventListener('click', () => this.show(button.dataset.view));
    }
    this.root
      .querySelector('.open-transfers')
      .addEventListener('click', () => this.show('torrents'));
    for (const button of this.root.querySelectorAll('[data-period]')) {
      button.addEventListener('click', () => {
        this.period = button.dataset.period;
        for (const item of this.root.querySelectorAll('[data-period]')) {
          item.setAttribute('aria-pressed', String(item === button));
        }
        this.renderHistory();
      });
    }
    this.show('overview');
    this.account.load();
    this.interval = new VisibleInterval(
      () => this.refresh(),
      10_000,
      () => this.refresh(true),
    );
    this.interval.run();
  }

  show(view) {
    this.view = view;
    this.root.dataset.view = view;
    this.logs.setActive(view === 'logs');
    this.root.hidden = view === 'torrents';
    for (const [name, page] of Object.entries(this.pages)) {
      page.hidden = name !== view;
    }
    for (const element of this.transferElements) {
      element.hidden = view !== 'torrents';
    }
    for (const button of this.navigation.querySelectorAll('[data-view]')) {
      if (button.dataset.view === view) {
        button.setAttribute('aria-current', 'page');
      } else {
        button.removeAttribute('aria-current');
      }
    }
    // Clusterize recalculates visible rows when returning from the dashboard.
    switch (view) {
      case 'torrents':
        globalThis.dispatchEvent(new Event('resize'));
        break;
      case 'overview':
        this.interval?.run(true);
        break;
      case 'logs':
        break;
      default:
        break;
    }
  }

  set(name, value) {
    const element = this.root.querySelector(`[data-value="${name}"]`);
    if (element.textContent !== value) {
      element.textContent = value;
    }
  }

  async refresh(resumed = false) {
    if (this.view !== 'overview') {
      return;
    }
    this.security.refresh();
    if (resumed) {
      this.root.dataset.stale = 'true';
      this.root.querySelector('.dash-sync').textContent = 'Állapot frissítése…';
      this.renderVpn({ state: 'unknown' });
      this.storage.markStale();
    }
    let received = false;
    await this.remote.sendRequest(
      {
        id: 'dashboard',
        jsonrpc: RPC._JsonRpcVersion,
        method: 'session_stats',
        params: { include_history: true },
      },
      (data) => {
        if (data?.result?.current_stats) {
          this.update(data.result);
          received = true;
        }
      },
      null,
      { quiet: true, timeout: 15_000 },
    );
    if (!received) {
      this.root.querySelector('.dash-sync').textContent =
        'Nincs kapcsolat · a megjelenített adatok elavultak';
      this.root.dataset.stale = 'true';
      this.renderVpn({ state: 'unknown' });
      this.storage.markStale();
    }
  }

  setConnectionState(state) {
    const sync = this.root.querySelector('.dash-sync');
    if (!sync) {
      return;
    }
    this.root.dataset.connection = state;
    if (state === 'connecting') {
      sync.textContent = 'Kapcsolódás a daemonhoz…';
    } else if (state === 'offline') {
      sync.textContent =
        'Nincs kapcsolat · újrapróbálkozás a következő frissítéskor';
    }
  }

  update(stats) {
    this.transmission.vpnStatus = stats.vpn_status ?? { state: 'unknown' };
    this.root.dataset.stale = 'false';
    this.root.dataset.connection = 'online';
    this.root.querySelector('.dash-sync').textContent =
      `Frissítve ${new Date().toLocaleTimeString('hu-HU')}`;
    this.set('download', `${size(stats.download_speed)}/s`);
    this.set('upload', `${size(stats.upload_speed)}/s`);
    this.set('active', String(stats.active_torrent_count));
    this.set(
      'total',
      `${stats.torrent_count} torrent · ${stats.paused_torrent_count} inaktív`,
    );
    const minutes = Math.floor(stats.current_stats.seconds_active / 60);
    const hours = Math.floor(minutes / 60);
    let uptime = `${minutes} p`;
    if (hours > 0) {
      uptime = `${hours} ó ${minutes % 60} p`;
    }
    if (hours >= 24) {
      uptime = `${Math.floor(hours / 24)} n ${hours % 24} ó`;
    }
    if (minutes === 0) {
      uptime = `${stats.current_stats.seconds_active} mp`;
    }
    this.set('uptime', uptime);
    this.set('lifetime-down', size(stats.cumulative_stats.downloaded_bytes));
    this.set('lifetime-up', size(stats.cumulative_stats.uploaded_bytes));
    this.history = stats.transfer_history;
    this.renderHistory();
    this.renderVpn(stats.vpn_status);
    this.storage.update(stats.storage_status);
  }

  renderVpn(vpn = {}) {
    const { label, tone } = vpnPresentation(vpn);
    const badge = this.root.querySelector('.vpn-badge');
    badge.textContent = label;
    badge.dataset.tone = tone;
    this.set(
      'vpn-provider',
      vpn.provider === 'purevpn' ? 'PureVPN' : (vpn.provider ?? '—'),
    );
    this.set(
      'vpn-protocol',
      vpn.protocol ? `OpenVPN / ${vpn.protocol.toUpperCase()}` : '—',
    );
    this.set('vpn-server', vpn.server_hostname ?? '—');
    this.set(
      'vpn-received',
      Number.isFinite(vpn.received_bytes) ? size(vpn.received_bytes) : '—',
    );
    this.set(
      'vpn-sent',
      Number.isFinite(vpn.sent_bytes) ? size(vpn.sent_bytes) : '—',
    );
    this.set(
      'vpn-packets',
      Number.isFinite(vpn.received_packets)
        ? [
            vpn.received_packets.toLocaleString('hu-HU'),
            vpn.sent_packets.toLocaleString('hu-HU'),
          ].join(' / ')
        : '—',
    );
    let endpoint = '—';
    if (vpn.server_endpoint) {
      endpoint = vpn.server_endpoint;
      if (vpn.server_port) {
        endpoint += `:${vpn.server_port}`;
      }
    }
    this.set('vpn-endpoint', endpoint);
    this.set('vpn-tunnel-ip', vpn.tunnel_ipv4 ?? '—');
    this.set(
      'vpn-guard',
      vpn.kill_switch === 'namespace-firewall'
        ? 'Névtér-tűzfal'
        : 'Nem ellenőrizhető',
    );
    this.set(
      'vpn-time',
      vpn.changed_at
        ? `${dateLabel(vpn.changed_at, { day: 'numeric', hour: '2-digit', minute: '2-digit', month: 'short' })} UTC`
        : '—',
    );
    this.root.querySelector('.vpn-note').textContent =
      vpn.state === 'unmanaged'
        ? 'A daemon nem a csomag VPN-felügyelőjével indult. Más VPN jelenlétét nem ellenőrizzük.'
        : 'Forgalom az aktuális tunnel létrehozása óta; újralétrehozáskor nullázódhat. A torrent-, DNS- és trackerforgalmat is tartalmazza, a külső titkosítási többletet nem.';
  }

  renderHistory() {
    if (!this.history) {
      this.root.querySelector('.history-note').textContent =
        'Az időszakos előzményekhez v7 vagy újabb daemon szükséges.';
      return;
    }
    const period = summarizePeriod(this.history, this.period);
    this.set('period-down', size(period.down));
    this.set('period-up', size(period.up));
    let ratio = period.up ? '∞' : '—';
    if (period.down) {
      ratio = (period.up / period.down).toLocaleString('hu-HU', {
        maximumFractionDigits: 2,
      });
    }
    this.set('ratio', ratio);
    const captions = {
      day: 'Mai nap · óránként',
      month: 'Aktuális hónap · naponta',
      week: 'Aktuális hét · hétfőtől vasárnapig',
    };
    this.root.querySelector('.period-caption').textContent =
      `${captions[this.period]} · UTC`;
    this.root.querySelector('.history-note').textContent =
      `${period.partial ? 'Részleges időszak. ' : ''}Adatgyűjtés kezdete: ${dateLabel(this.history.started_at, { day: 'numeric', hour: '2-digit', minute: '2-digit', month: 'short', year: 'numeric' })} UTC. ${period.down + period.up === 0 ? 'Ebben az időszakban még nincs rögzített forgalom.' : 'Az értékek torrentadat-forgalmat mutatnak.'}`;
    const max = Math.max(
      1,
      ...period.points.map((point) => Math.max(point.down, point.up)),
    );
    this.root.querySelector('.chart-scale').textContent =
      `Skála: ${size(max === 1 ? 0 : max)}`;
    const chart = this.root.querySelector('.transfer-chart');
    const rows = this.root.querySelector('tbody');
    const bars = document.createDocumentFragment();
    const table = document.createDocumentFragment();
    for (const point of period.points) {
      const label = dateLabel(
        point.time,
        this.period === 'day'
          ? { hour: '2-digit', minute: '2-digit' }
          : { day: 'numeric', month: 'short' },
      );
      const group = document.createElement('div');
      group.className = 'chart-group';
      group.dataset.available = String(point.available);
      group.title = `${label} UTC · ↓ ${size(point.down)} · ↑ ${size(point.up)}${point.available ? '' : ' · nincs adat'}`;
      group.setAttribute('aria-hidden', 'true');
      for (const direction of ['down', 'up']) {
        const bar = document.createElement('span');
        bar.className = `chart-bar ${direction}`;
        bar.style.height = `${(point[direction] / max) * 100}%`;
        group.append(bar);
      }
      bars.append(group);
      const row = document.createElement('tr');
      for (const text of [
        label,
        point.available ? size(point.down) : '—',
        point.available ? size(point.up) : '—',
      ]) {
        const cell = document.createElement('td');
        cell.textContent = text;
        row.append(cell);
      }
      table.append(row);
    }
    chart.replaceChildren(bars);
    rows.replaceChildren(table);
    const labels = this.root.querySelector('.chart-labels');
    labels.replaceChildren(
      ...[
        period.points[0],
        period.points[Math.floor(period.points.length / 2)],
        period.points.at(-1),
      ].map((point) => {
        const label = document.createElement('span');
        label.textContent = dateLabel(
          point.time,
          this.period === 'day'
            ? { hour: '2-digit', minute: '2-digit' }
            : { day: 'numeric', month: 'short' },
        );
        return label;
      }),
    );
  }
}
