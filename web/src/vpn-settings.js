import { TelemetrySettings } from './telemetry-settings.js';

export class VpnSettings extends TelemetrySettings {
  constructor(remote) {
    super(remote, {
      fields: [
        ['provider', 'VPN-szolgáltató', 'text', 'purevpn'],
        ['username', 'VPN-felhasználónév', 'text', ''],
        ['password', 'VPN-jelszó', 'password', ''],
        ['openvpn_profile', 'OpenVPN-profil tartalma (.ovpn)', 'textarea', ''],
        [
          'openvpn_config',
          'Vagy meglévő .ovpn fájl abszolút útvonala',
          'text',
          '',
        ],
        [
          'credentials_file',
          'Vagy hitelesítőfájl abszolút útvonala',
          'text',
          '',
        ],
        ['dns', 'DNS-szerverek, vesszővel elválasztva', 'list', []],
        ['rpc_port', 'Helyi RPC-port', 'number', 9091],
        ['start_timeout', 'Kapcsolódási időkorlát (másodperc)', 'number', 90],
        ['vpn_log_retention', 'VPN-napló megőrzése (sor)', 'number', 256],
        [
          'vpn_log_flush_seconds',
          'VPN-napló frissítése (másodperc)',
          'number',
          2,
        ],
        ['subnet', 'Belső VPN-alhálózat', 'text', ''],
      ],
      target: 'vpn',
    });
    this.root.classList.add('vpn-settings');
    this.root.querySelector('summary').textContent =
      'Adminisztráció · VPN / PureVPN';
    this.root.querySelector('p').textContent =
      'A VPN-beállítások a config könyvtár vpn.json fájljába kerülnek. A mentés nem állítja le és nem kapcsolja át a jelenlegi kapcsolatot. VPN-es indításkor a meglévő kill switch védi a forgalmat.';
    this.root.querySelector('[data-action="test"]').textContent =
      'VPN-profil ellenőrzése';
    const connect = document.createElement('button');
    connect.type = 'button';
    connect.dataset.action = 'connect_test';
    connect.textContent = 'VPN-kapcsolat tesztelése';
    connect.addEventListener('click', () =>
      this.perform(() => this.apply('connect_test')),
    );
    this.root.querySelector('[data-action="test"]').after(connect);
    const note = document.createElement('p');
    note.textContent =
      'A kapcsolatteszt a szerkesztett adatokkal, külön hálózati névtérben hitelesít és VPN-alagutat épít, majd bontja azt. Legfeljebb 60 másodpercig próbálkozik; a futó torrentek kapcsolatát nem módosítja.';
    this.form.before(note);
    const serverNote = document.createElement('p');
    serverNote.textContent =
      'A Transmission indítási adatait automatikusan a szerver konfigurációjából vesszük át. A letöltési könyvtárat az általános Transmission-beállításokban lehet módosítani. A VPN JSON importálása nem írja felül a helyi indítási adatokat.';
    this.form.before(serverNote);
    this.root.querySelector('button[type="submit"]').textContent =
      'VPN-beállítások mentése';
    for (const group of this.root.querySelectorAll('fieldset')) {
      if (group.querySelectorAll('label').length === 0) {
        group.remove();
      }
    }
    this.root.querySelector('legend').textContent = 'PureVPN · OpenVPN';
    const advanced = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Haladó hálózati és naplóbeállítások';
    const advancedFields = document.createElement('fieldset');
    advanced.append(summary, advancedFields);
    for (const key of [
      'rpc_port',
      'subnet',
      'vpn_log_retention',
      'vpn_log_flush_seconds',
    ]) {
      advancedFields.append(this.form.elements.namedItem(key).closest('label'));
    }
    this.root.querySelector('.telemetry-fields').append(advanced);
    const label = document.createElement('label');
    label.textContent = 'OpenVPN-profil betöltése (.ovpn)';
    const upload = document.createElement('input');
    upload.type = 'file';
    upload.accept = '.ovpn,text/plain';
    label.append(upload);
    this.root.querySelector('.telemetry-actions').append(label);
    upload.addEventListener('change', () =>
      this.perform(async () => {
        const [file] = upload.files;
        upload.value = '';
        if (!file) {
          return;
        }
        if (file.size > 50_000) {
          throw new Error('A profil legfeljebb 50 kB lehet.');
        }
        const text = await file.text();
        this.form.elements.namedItem('openvpn_profile').value = text;
        this.status.textContent =
          'Profil betöltve a szerkesztőbe. Add meg a VPN-hitelesítést, majd ellenőrizd és mentsd.';
      }),
    );
  }

  configuration(response) {
    const config = {
      ...response.vpn_defaults,
      ...response.vpn_configuration,
    };
    this.serverConfiguration = config;
    return config;
  }

  collect() {
    const config = super.collect();
    for (const key of ['run_user', 'daemon', 'config_dir', 'download_dir']) {
      delete config[key];
      if (Object.hasOwn(this.serverConfiguration ?? {}, key)) {
        config[key] = this.serverConfiguration[key];
      }
    }
    return config;
  }

  async apply(action) {
    // Refresh deployment paths even after importing a configuration from another server.
    this.configuration(await this.call('telemetry_config_get'));
    await super.apply(action);
  }
}
