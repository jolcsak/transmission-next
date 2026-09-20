export class RpcSecuritySettings {
  constructor(remote) {
    this.remote = remote;
    this.root = document.createElement('details');
    this.root.className = 'dash-panel telemetry-settings rpc-security-settings';
    this.root.innerHTML = `<summary>RPC hitelesítés</summary>
      <p>A Transmission RPC és webes felülete HTTP-hitelesítést kér. A jelszó csak egyszer jut el a daemonhoz, sózott ellenőrzőértékként mentődik, és sem lekérdezni, sem exportálni nem lehet.</p>
      <p class="rpc-security-state" role="status" aria-live="polite">Állapot lekérdezésekor jelenik meg.</p>
      <form><div class="telemetry-fields"><fieldset><legend>Belépési adatok</legend>
        <label>Felhasználónév <input name="username" type="text" minlength="1" maxlength="64" required autocomplete="username"></label>
        <label>Új jelszó <input name="password" type="password" minlength="8" maxlength="4096" required autocomplete="new-password"></label>
        <label>Új jelszó ismét <input name="password-confirmation" type="password" minlength="8" maxlength="4096" required autocomplete="new-password"></label>
      </fieldset></div><div class="telemetry-actions"><button type="button" data-action="load">Állapot frissítése</button><button type="submit">Mentés és hitelesítés bekapcsolása</button></div></form>
      <p class="telemetry-status" role="status" aria-live="polite"></p>`;
    this.form = this.root.querySelector('form');
    this.status = this.root.querySelector('.telemetry-status');
    this.state = this.root.querySelector('.rpc-security-state');
    this.root.addEventListener('toggle', () => {
      if (this.root.open && !this.loaded) {
        this.perform(() => this.load());
      }
    });
    this.root
      .querySelector('[data-action="load"]')
      .addEventListener('click', () => this.perform(() => this.load()));
    this.form.addEventListener('submit', (event) => {
      event.preventDefault();
      this.perform(() => this.save());
    });
  }

  async call(method, params = {}) {
    let payload = null;
    await this.remote.sendRequest(
      { id: 'rpc-security', jsonrpc: '2.0', method, params },
      (response) => {
        payload = response;
      },
      this,
      { quiet: true, timeout: 10_000 },
    );
    if (!payload || payload.error) {
      throw new Error(payload?.error?.message ?? 'A daemon nem érhető el.');
    }
    return payload.result;
  }

  render({ enabled, username }) {
    this.state.textContent = enabled
      ? `Hitelesítés aktív: ${username || 'névtelen fiók'}.`
      : 'Hitelesítés még nincs bekapcsolva. A daemon jelenleg csak a helyi gépről érhető el.';
    this.form.elements.username.value = username ?? '';
  }

  async load() {
    this.render(await this.call('rpc_security_get'));
    this.loaded = true;
    this.status.textContent = '';
  }

  async save() {
    const username = this.form.elements.username.value.trim();
    const password = this.form.elements.password.value;
    const confirmation = this.form.elements['password-confirmation'].value;
    if (password !== confirmation) {
      throw new Error('A két új jelszó nem egyezik.');
    }
    if (password.length < 8) {
      throw new Error('Az új jelszó legalább 8 karakter legyen.');
    }
    const result = await this.call('rpc_security_set', { password, username });
    this.form.elements.password.value = '';
    this.form.elements['password-confirmation'].value = '';
    this.render(result);
    this.status.textContent = `Mentve. A következő RPC-kérésnél a böngésző ${result.username} felhasználóval új bejelentkezést kér.`;
  }

  async perform(action) {
    if (this.busy) {
      return;
    }
    this.busy = true;
    for (const input of this.form.elements) {
      input.disabled = true;
    }
    try {
      await action();
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.busy = false;
      for (const input of this.form.elements) {
        input.disabled = false;
      }
    }
  }
}
