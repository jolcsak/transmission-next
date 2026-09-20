"""Unprivileged VPN configuration validation; never changes routes or starts VPN."""
import json
import os
from pathlib import Path
import pwd
import sys
import socket
import tempfile

location = Path(__file__).resolve().parent.parent
provider_dir = location / 'vpn'
if not (provider_dir / 'controller.py').is_file():
    provider_dir = location / 'extras/vpn'
sys.path.insert(0, str(provider_dir))
import controller as vpn_controller
from vpn_environment import overrides as environment_overrides, resolve as resolve_environment


def defaults(directory):
    return dict(provider='purevpn', openvpn_config='', credentials_file='',
        run_user=pwd.getpwuid(os.getuid()).pw_name,
        daemon=str(location / 'bin/transmission-daemon'), config_dir=str(Path(directory).resolve()),
        download_dir=str(Path(directory).resolve().parent / 'downloads'),
        dns=['1.1.1.1', '1.0.0.1'], rpc_port=9091, subnet='10.203.74.0/30', start_timeout=90,
        vpn_log_retention=256, vpn_log_flush_seconds=2)


def validate(config, directory):
    if not isinstance(config, dict) or Path(config.get('config_dir', '')).resolve() != Path(directory).resolve():
        raise ValueError('VPN must use this daemon configuration directory')
    # Temporary files stay private; no root operations, credentials sent, or DNS probes.
    with tempfile.TemporaryDirectory(prefix='.vpn-check-', dir=directory) as folder:
        path = Path(folder) / 'vpn.json'
        path.write_text(json.dumps(config))
        parsed, user = vpn_controller.load_config(path)
        profile_path = parsed['openvpn_config']
        if parsed.get('openvpn_profile'):
            profile_path = Path(folder) / 'profile.ovpn'
            profile_path.write_text(parsed['openvpn_profile'])
        profile = vpn_controller.PROVIDERS[parsed['provider']].load(profile_path)
        if not parsed.get('username'):
            vpn_controller.credentials(parsed['credentials_file'])
        if not Path(parsed['daemon']).is_file() or not os.access(parsed['daemon'], os.X_OK):
            raise ValueError('Transmission executable does not exist')
        return {'profile': 'ok', 'credentials_format': 'ok', 'transport': profile.protocol,
                'server': profile.hostname, 'port': profile.port,
                'note': 'Local validation only; account authentication and VPN connection were not tested.'}


def test_connection(config, directory):
    validate(config, directory)
    # Resolve external certificate/credential files as the daemon user, never as root.
    with tempfile.TemporaryDirectory(prefix='.vpn-test-', dir=directory) as folder:
        profile_path = config.get('openvpn_config')
        if config.get('openvpn_profile'):
            profile_path = Path(folder) / 'profile.ovpn'
            profile_path.write_text(config['openvpn_profile'])
        profile = vpn_controller.PROVIDERS[config['provider']].load(profile_path)
        secret = (config['username'] + '\n' + config['password'] + '\n' if config.get('username')
                  else vpn_controller.credentials(config['credentials_file']))
        request = dict(profile=profile.crypto + f'\nremote {profile.hostname} {profile.port}\nproto {profile.protocol}\n',
                       secret=secret, timeout=min(config.get('start_timeout', 90), 60))
        payload = json.dumps(request).encode()
        if len(payload) > 65536:
            raise ValueError('Normalized test profile is too large')
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(125)
            try:
                client.connect('/run/transmission-vpn-test-control/probe.sock')
            except OSError:
                return {'state': 'error', 'message': (
                    'A VPN-kapcsolatteszt socketje nem érhető el. Dockerben nincs külön '
                    'transmission-vpn-test szolgáltatás: hozd létre újra ezt a konténert '
                    '/dev/net/tun eszközzel, NET_ADMIN és SYS_ADMIN capabilitykkel, '
                    'net.ipv4.ip_forward=1 beállítással és DSM-en AppArmor unconfined módban. '
                    'Ezután a konténer automatikusan elindítja a tesztsegédet.')}
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            data = bytearray()
            while len(data) <= 4096:
                chunk = client.recv(4097 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            result = json.loads(data) if len(data) <= 4096 else {}
            if result.get('state') != 'tested':
                return {'state': 'error', 'message': 'A VPN-kapcsolatteszt sikertelen vagy túllépte az időkorlátot. Ellenőrizd a profilt, a VPN-fiók adatait és a szerver elérhetőségét.'}
            return result
