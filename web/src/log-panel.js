import { VisibleInterval } from './visible-interval.js';

const levels = [
  '',
  'Kritikus',
  'Hiba',
  'Figyelmeztetés',
  'Információ',
  'Debug',
  'Trace',
];

export class LogPanel {
  constructor(remote) {
    this.remote = remote;
    this.entries = [];
    this.root = document.createElement('details');
    this.root.className = 'dash-panel log-panel';
    this.root.innerHTML = `<summary>Transmission és VPN napló</summary>
      <p>A Transmission és a VPN megőrzött naplóbejegyzései. Nyitott naplónál két másodpercenként frissül; háttérben szünetel a lekérdezés.</p>
      <div class="log-controls">
        <label>Keresés az üzenetben és forrásban <input type="search" maxlength="256" name="log-search" placeholder="Pl. tracker, disk, error"></label>
        <label>Naplószint <select name="log-level"><option value="0">Minden szint</option>${levels
          .slice(1)
          .map(
            (label, index) => `<option value="${index + 1}">${label}</option>`,
          )
          .join('')}</select></label>
        <label>Rendezés <select name="log-sort"><option value="newest">Legújabb elöl</option><option value="oldest">Legrégebbi elöl</option><option value="level">Súlyosság szerint</option><option value="source">Forrás A–Z</option></select></label>
        <button type="button">Frissítés</button>
      </div>
      <p role="status" aria-live="polite">Még nincs betöltve.</p>
      <div class="table-scroll"><table><thead><tr><th>Idő (helyi)</th><th>Szint</th><th>Forrás</th><th>Üzenet</th></tr></thead><tbody></tbody></table></div>`;
    this.status = this.root.querySelector('[role="status"]');
    this.button = this.root.querySelector('button');
    this.body = this.root.querySelector('tbody');
    this.search = this.root.querySelector('[name="log-search"]');
    this.level = this.root.querySelector('[name="log-level"]');
    this.sort = this.root.querySelector('[name="log-sort"]');
    this.search.addEventListener('input', () => this.render());
    this.level.addEventListener('change', () => this.render());
    this.sort.addEventListener('change', () => this.render());
    this.button.addEventListener('click', () => this.refresh());
    this.root.addEventListener('toggle', () => {
      if (this.root.open) {
        this.refresh();
      }
    });
    this.active = false;
  }

  async refresh() {
    if (this.button.disabled) {
      return;
    }
    this.button.disabled = true;
    this.status.textContent = 'Napló betöltése…';
    try {
      let payload = null;
      await this.remote.sendRequest(
        { id: 'log-view', jsonrpc: '2.0', method: 'log_get', params: {} },
        (response) => {
          payload = response;
        },
        this,
        { quiet: true, timeout: 10_000 },
      );
      if (!payload?.result || payload.error) {
        throw new Error(payload?.error?.message ?? 'A napló nem érhető el.');
      }
      const vpnEntries = Array.isArray(payload.result.vpn_entries?.entries)
        ? payload.result.vpn_entries.entries
        : [];
      const incoming = [...payload.result.entries, ...vpnEntries];
      const unchanged =
        incoming.length === this.previousEntries?.length &&
        incoming.every((entry, index) => {
          const previous = this.previousEntries[index];
          return (
            entry.time_ms === previous.time_ms &&
            entry.level === previous.level &&
            entry.source === previous.source &&
            entry.message === previous.message
          );
        });
      this.loadedAt = new Date().toLocaleTimeString('hu-HU');
      if (unchanged) {
        this.status.textContent = `${this.lastSummary} Frissítve: ${this.loadedAt}.`;
        return;
      }
      this.previousEntries = incoming;
      this.entries = incoming
        .toSorted((a, b) => a.time_ms - b.time_ms)
        .slice(-2512)
        .map((entry, index) => ({
          ...entry,
          index,
        }));
      this.loadedAt = new Date().toLocaleTimeString('hu-HU');
      this.render();
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.button.disabled = false;
    }
  }

  render() {
    const query = this.search.value.trim().toLocaleLowerCase('hu-HU');
    const level = Number(this.level.value);
    const rows = this.entries.filter(
      (entry) =>
        (level === 0 || entry.level === level) &&
        `${entry.source} ${entry.message}`
          .toLocaleLowerCase('hu-HU')
          .includes(query),
    );
    const newest = (a, b) => b.time_ms - a.time_ms || b.index - a.index;
    const comparators = {
      level: (a, b) => a.level - b.level || newest(a, b),
      newest,
      oldest: (a, b) => -newest(a, b),
      source: (a, b) => a.source.localeCompare(b.source, 'hu') || newest(a, b),
    };
    rows.sort(comparators[this.sort.value]);
    const fragment = document.createDocumentFragment();
    for (const entry of rows) {
      const row = document.createElement('tr');
      row.dataset.level = entry.level;
      for (const value of [
        new Date(entry.time_ms).toLocaleString('hu-HU'),
        levels[entry.level] ?? 'Ismeretlen',
        entry.source,
        entry.message,
      ]) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      }
      fragment.append(row);
    }
    this.body.replaceChildren(fragment);
    this.lastSummary = `${rows.length} találat / ${this.entries.length} betöltött bejegyzés. A szűrés a betöltött naplórészletre vonatkozik.`;
    this.status.textContent =
      this.lastSummary + (this.loadedAt ? ` Frissítve: ${this.loadedAt}.` : '');
  }

  setActive(active) {
    this.active = active;
    this.timer?.stop();
    if (active) {
      this.root.open = true;
      this.refresh();
      this.timer = new VisibleInterval(() => {
        if (this.active && this.root.open) {
          return this.refresh();
        }
        return null;
      }, 2000);
    }
  }
}
