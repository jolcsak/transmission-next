export class AccountMenu {
  constructor(remote, { onChangePassword } = {}) {
    this.remote = remote;
    this.onChangePassword = onChangePassword;
    this.root = document.createElement('div');
    this.root.className = 'account-menu';
    this.root.innerHTML = `<button type="button" class="account-trigger" aria-expanded="false" aria-haspopup="menu">
        <span class="account-avatar" aria-hidden="true">?</span>
        <span class="account-identity"><small>BEJELENTKEZVE</small><strong>Betöltés…</strong></span>
        <span class="account-chevron" aria-hidden="true">⌄</span>
      </button>
      <div class="account-popover" role="menu" hidden>
        <div class="account-summary"><span class="account-avatar" aria-hidden="true">?</span><div><small>Aktív felhasználó</small><strong>Betöltés…</strong></div></div>
        <button type="button" role="menuitem" data-action="password">Jelszó módosítása</button>
        <button type="button" role="menuitem" data-action="logout" class="account-logout">Kijelentkezés</button>
        <p class="account-status" role="status" aria-live="polite"></p>
      </div>`;
    this.trigger = this.root.querySelector('.account-trigger');
    this.popover = this.root.querySelector('.account-popover');
    this.status = this.root.querySelector('.account-status');
    this.trigger.addEventListener('click', () => this.toggle());
    this.root
      .querySelector('[data-action="password"]')
      .addEventListener('click', () => {
        this.close();
        this.onChangePassword?.();
      });
    this.root
      .querySelector('[data-action="logout"]')
      .addEventListener('click', () => this.logout());
    document.addEventListener('click', (event) => {
      if (!this.root.contains(event.target)) {
        this.close();
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        this.close(true);
      }
    });
  }

  async call(method) {
    let payload = null;
    await this.remote.sendRequest(
      { id: 'account-menu', jsonrpc: '2.0', method, params: {} },
      (response) => {
        payload = response;
      },
      this,
      { quiet: true, timeout: 10_000 },
    );
    if (!payload || payload.error) {
      throw new Error(payload?.error?.message ?? 'A fiók nem kérdezhető le.');
    }
    return payload.result;
  }

  async load() {
    try {
      this.render(await this.call('rpc_security_get'));
      this.status.textContent = '';
    } catch (error) {
      this.status.textContent = error.message;
      this.render({ enabled: false, username: 'Nem elérhető' });
    }
  }

  render({ enabled, username }) {
    const label = enabled && username ? username : 'Helyi hozzáférés';
    const initial = label.trim().charAt(0).toLocaleUpperCase('hu-HU') || '?';
    for (const element of this.root.querySelectorAll('.account-avatar')) {
      element.textContent = initial;
    }
    for (const element of this.root.querySelectorAll(
      '.account-identity strong, .account-summary strong',
    )) {
      element.textContent = label;
    }
    this.root.dataset.authenticated = String(Boolean(enabled));
  }

  toggle() {
    const open = this.popover.hidden;
    this.popover.hidden = !open;
    this.trigger.setAttribute('aria-expanded', String(open));
    if (open) {
      this.popover.querySelector('[role="menuitem"]').focus();
    }
  }

  close(restoreFocus = false) {
    if (this.popover.hidden) {
      return;
    }
    this.popover.hidden = true;
    this.trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) {
      this.trigger.focus();
    }
  }

  logout() {
    if (this.busy) {
      return;
    }
    this.busy = true;
    this.status.textContent = 'Kijelentkezés…';
    const url = new URL('../logout', globalThis.location.href);
    url.searchParams.set('at', Date.now());
    globalThis.location.replace(url);
  }
}
