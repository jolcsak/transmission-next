const fields = [
  ['interval_seconds', 'Mintavétel (másodperc)', 'number', 30],
  ['heartbeat_seconds', 'MQTT életjel (másodperc)', 'number', 300],
  ['include_history', 'MQTT forgalmi előzmények', 'checkbox', false],
  [
    'rpc.url',
    'Daemon API címe',
    'url',
    'http://127.0.0.1:9091/transmission/rpc',
  ],
  ['rpc.username', 'API felhasználónév', 'text', ''],
  ['rpc.password', 'API jelszó', 'password', ''],
  ['rpc.password_file', 'API jelszófájl (opcionális)', 'text', ''],
  ['mqtt.enabled', 'MQTT engedélyezve', 'checkbox', false],
  ['mqtt.host', 'MQTT szerver', 'text', ''],
  ['mqtt.port', 'MQTT port', 'number', 8883],
  ['mqtt.tls', 'MQTT TLS', 'checkbox', true],
  ['mqtt.username', 'MQTT felhasználónév', 'text', ''],
  ['mqtt.password', 'MQTT jelszó', 'password', ''],
  ['mqtt.password_file', 'MQTT jelszófájl (opcionális)', 'text', ''],
  ['mqtt.ca_file', 'MQTT CA-fájl (opcionális)', 'text', ''],
  ['mqtt.cert_file', 'MQTT kliens tanúsítvány (opcionális)', 'text', ''],
  ['mqtt.key_file', 'MQTT kliens privát kulcs fájlja (opcionális)', 'text', ''],
  ['mqtt.client_id', 'MQTT kliensazonosító', 'text', 'transmission-telemetry'],
  ['mqtt.topic_prefix', 'MQTT topic előtag', 'text', 'home/transmission'],
  ['influxdb.enabled', 'InfluxDB engedélyezve', 'checkbox', false],
  ['influxdb.url', 'InfluxDB URL', 'url', 'https://influx.example.com:8086'],
  ['influxdb.org', 'InfluxDB szervezet', 'text', ''],
  ['influxdb.bucket', 'InfluxDB bucket', 'text', ''],
  ['influxdb.token', 'InfluxDB token', 'password', ''],
  ['influxdb.token_file', 'InfluxDB tokenfájl (opcionális)', 'text', ''],
  ['influxdb.ca_file', 'InfluxDB CA-fájl (opcionális)', 'text', ''],
  ['influxdb.instance', 'Gépazonosító', 'text', 'torrent-server'],
  ['influxdb.flush_seconds', 'InfluxDB kötegküldés (másodperc)', 'number', 60],
  ['influxdb.buffer_bytes', 'InfluxDB puffer (bájt)', 'number', 262_144],
  [
    'influxdb.include_directories',
    'Könyvtárankénti InfluxDB-metrikák',
    'checkbox',
    false,
  ],
];

export class TelemetrySettings {
  constructor(remote, options = {}) {
    this.fields = options.fields ?? fields;
    this.target = options.target ?? 'telemetry';
    this.remote = remote;
    this.draft = {};
    this.root = document.createElement('details');
    this.root.className = 'dash-panel telemetry-settings';
    this.root.innerHTML = `<summary>Adminisztráció · MQTT és InfluxDB beállítások</summary>
      <p>A beállítások a daemon config könyvtárának telemetry.json fájljába kerülnek. A kapcsolatteszt MQTT-tesztüzenetet és InfluxDB-tesztpontot küld.</p>
      <p>A jelszavak és tokenek a JSON-ban és az exportált fájlban is szerepelnek. A felületet csak megbízható adminisztrátorok érjék el.</p>
      <p class="telemetry-environment" role="note"></p>
      <form><div class="telemetry-fields"></div><div class="telemetry-actions">
      <button type="button" data-action="load">Újratöltés</button>
      <button type="button" data-action="test">Kapcsolatok tesztelése</button>
      <button type="submit">Mentés és alkalmazás</button>
      <button type="button" data-action="export">JSON exportálása</button>
      <label>JSON importálása <input type="file" accept="application/json,.json"></label>
      </div></form><p class="telemetry-status" role="status" aria-live="polite"></p>`;
    this.form = this.root.querySelector('form');
    this.status = this.root.querySelector('.telemetry-status');
    const groups = new Map();
    for (const [name, title] of [
      ['general', 'Mintavétel'],
      ['rpc', 'Daemon API'],
      ['mqtt', 'MQTT'],
      ['influxdb', 'InfluxDB'],
    ]) {
      const group = document.createElement('fieldset');
      const legend = document.createElement('legend');
      legend.textContent = title;
      group.append(legend);
      this.root.querySelector('.telemetry-fields').append(group);
      groups.set(name, group);
    }
    for (const [key, title, type] of this.fields) {
      const label = document.createElement('label');
      label.append(document.createTextNode(title));
      const input = document.createElement(
        type === 'textarea' ? 'textarea' : 'input',
      );
      input.name = key;
      if (type !== 'textarea') {
        input.type = type === 'list' ? 'text' : type;
      }
      if (type === 'password') {
        input.autocomplete = 'new-password';
      }
      label.append(input);
      groups
        .get(key.includes('.') ? key.split('.')[0] : 'general')
        .append(label);
    }
    this.render({});
    this.root.addEventListener('toggle', () => {
      if (this.root.open && !this.loaded) {
        this.perform(() => this.load());
      }
    });
    this.form.addEventListener('submit', (event) => {
      event.preventDefault();
      this.perform(() => this.apply('save'));
    });
    this.root
      .querySelector('[data-action="load"]')
      .addEventListener('click', () => this.perform(() => this.load()));
    this.root
      .querySelector('[data-action="test"]')
      .addEventListener('click', () => this.perform(() => this.apply('test')));
    this.root
      .querySelector('[data-action="export"]')
      .addEventListener('click', () =>
        this.perform(() => {
          const url = URL.createObjectURL(
            new Blob([JSON.stringify(this.collect(), null, 2)], {
              type: 'application/json',
            }),
          );
          const link = document.createElement('a');
          link.href = url;
          link.download = `${this.target}.json`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          this.status.textContent =
            'JSON exportálva. A fájl titkos adatokat is tartalmazhat.';
        }),
      );
    this.root
      .querySelector('input[type="file"]')
      .addEventListener('change', (event) =>
        this.perform(async () => {
          const [file] = event.target.files;
          event.target.value = '';
          if (!file) {
            return;
          }
          if (file.size > 60_000) {
            throw new Error('A JSON-fájl túl nagy (maximum 60 kB).');
          }
          const config = JSON.parse(await file.text());
          if (!config || typeof config !== 'object' || Array.isArray(config)) {
            throw new Error('JSON-objektum szükséges.');
          }
          this.render(config);
          this.loaded = true;
          this.status.textContent =
            'Importálva a szerkesztőbe. Ellenőrizd, majd mentsd a beállításokat.';
        }),
      );
  }

  render(config) {
    if (config.mqtt?.host && !Object.hasOwn(config.mqtt, 'enabled')) {
      config = structuredClone(config);
      config.mqtt.enabled = true;
    }
    this.draft = structuredClone(config);
    for (const [key, , type, fallback] of this.fields) {
      const value =
        key.split('.').reduce((object, part) => object?.[part], config) ??
        fallback;
      const input = this.form.elements.namedItem(key);
      if (type === 'checkbox') {
        input.checked = Boolean(value);
      } else {
        input.value =
          type === 'list' && Array.isArray(value) ? value.join(', ') : value;
      }
    }
  }

  collect() {
    const config = structuredClone(this.draft);
    for (const [key, , type] of this.fields) {
      const input = this.form.elements.namedItem(key);
      const parts = key.split('.');
      const name = parts.pop();
      if (parts.length > 0) {
        config[parts[0]] ??= {};
      }
      const target = parts.length > 0 ? config[parts[0]] : config;
      if (type === 'checkbox') {
        target[name] = input.checked;
      } else if (input.value === '') {
        delete target[name];
      } else {
        if (type === 'list') {
          target[name] = input.value
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean);
        } else {
          target[name] = type === 'number' ? Number(input.value) : input.value;
        }
      }
    }
    return config;
  }

  async call(method, params = {}) {
    let payload = null;
    await this.remote.sendRequest(
      { id: 'telemetry-admin', jsonrpc: '2.0', method, params },
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

  configuration(response) {
    if (this.target !== 'vpn') {
      return response.configuration;
    }
    return Object.keys(response.vpn_configuration).length > 0
      ? response.vpn_configuration
      : response.vpn_defaults;
  }

  async load() {
    const response = await this.call('telemetry_config_get');
    this.showEnvironment(response.job);
    this.render(this.configuration(response));
    this.loaded = true;
    this.status.textContent = `Betöltve: ${this.target === 'vpn' ? response.vpn_file : response.file}`;
  }

  showEnvironment(job) {
    if (this.target === 'vpn') {
      const names = job.vpn_environment_overrides ?? [];
      this.root.querySelector('.telemetry-environment').textContent =
        names.length > 0
          ? `VPN környezeti felülírások: ${names.join(', ')}. A teszt az env értékeit használja; a szerkesztő, mentés és export a JSON-t kezeli. Az env módosítása után indítsd újra az admin- és VPN-felügyelőt, azonos környezettel.`
          : 'VPN-sorrend: környezeti változók → JSON → alapértékek.';
      return;
    }
    const names = job.environment_overrides ?? [];
    this.root.querySelector('.telemetry-environment').textContent =
      names.length > 0
        ? `Környezeti felülírások aktívak: ${names.join(', ')}. A mentés és export a JSON-beállításokra vonatkozik; futáskor és teszteléskor az env értékek érvényesek. Módosításukhoz indítsd újra a felügyelőt.`
        : 'Sorrend: környezeti változók → mentett JSON → alapértékek.';
  }

  async apply(action) {
    const jobId = [
      ...globalThis.crypto.getRandomValues(new Uint32Array(4)),
    ].join('-');
    await this.call('telemetry_config_apply', {
      action,
      configuration: JSON.stringify(this.collect()),
      job_id: jobId,
      target: this.target,
    });
    this.status.textContent =
      action === 'connect_test'
        ? 'VPN-kapcsolódás és hitelesítés tesztelése…'
        : 'A telemetria-felügyelő válaszára várunk…';
    const attempts = action === 'connect_test' ? 140 : 60;
    for (let attempt = 0; attempt < attempts; ++attempt) {
      await new Promise((resolve) => {
        setTimeout(resolve, 1000);
      });
      const response = await this.call('telemetry_config_get');
      this.showEnvironment(response.job);
      if (response.job.job_id !== jobId) {
        continue;
      }
      if (response.job.state === 'error') {
        throw new Error(response.job.message);
      }
      if (action === 'save') {
        this.render(this.configuration(response));
        this.status.textContent =
          this.target === 'vpn'
            ? `Mentve: ${response.vpn_file}. A következő VPN-es indításkor lép életbe; a kapcsolat most nem változott.`
            : `Mentve és alkalmazva: ${response.file}`;
      } else if (action === 'connect_test') {
        this.status.textContent = `Sikeres VPN-hitelesítés és alagút (${response.job.elapsed_ms} ms, bontással együtt). A tesztkapcsolat lezárva. A torrentek kapcsolata változatlan.`;
      } else {
        this.status.textContent =
          this.target === 'vpn'
            ? 'A VPN-profil és a hitelesítési adatok formátuma rendben. Valódi VPN-kapcsolat és fiókhitelesítés nem történt.'
            : Object.entries(response.job.checks)
                .map(
                  ([key, value]) =>
                    `${key}: ${value === 'ok' ? 'sikeres' : 'sikertelen'}`,
                )
                .join(' · ');
      }
      return;
    }
    throw new Error(
      'Nincs válasz. Ellenőrizd, hogy fut-e a telemetria-felügyelő a daemon config könyvtárával. A kérés még függőben lehet.',
    );
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
