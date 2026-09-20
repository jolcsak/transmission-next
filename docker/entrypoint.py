"""Container lifecycle: fail closed by default; never silently disable configured VPN."""
from collections import deque
import json
import os
from pathlib import Path
import signal
import tempfile
import subprocess
import sys
import threading
import time

ROOT = Path('/opt/transmission')
CONFIG = Path('/config')
STOP = threading.Event()
CHILDREN = []
SECURITY = None


class StartupError(ValueError):
    """Only explicit, secret-free diagnostics may be printed to Docker logs."""
    def __init__(self, code, message):
        self.code = code
        self.safe_message = message
        super().__init__(message)


def failure_message(error):
    if isinstance(error, StartupError):
        return f'Container configuration error [{error.code}]: {error.safe_message}'
    if isinstance(error, PermissionError):
        return ('Container configuration error [filesystem_permission]: Check /config and /downloads '
                'mount permissions, secret-file access and the container root user.')
    return ('Container startup/service failure [' + type(error).__name__ + ']: '
            'Check the preceding service log and VPN status. Raw exception details are hidden to protect credentials.')


def read(path, fallback=None):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else dict(fallback or {})
    except (json.JSONDecodeError, UnicodeError):
        raise StartupError('invalid_json', f'{path.name} must contain valid UTF-8 JSON; its contents are not logged.') from None
    if not isinstance(value, dict):
        raise StartupError('invalid_json_object', f'{path.name} must contain a JSON object, not an array or scalar.')
    return value


def write(path, value, uid=1000, gid=1000):
    content = json.dumps(value, indent=2) + '\n'
    if path.exists() and path.read_text() == content:
        return
    fd, filename = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    temp = Path(filename)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            os.fchown(stream.fileno(), uid, gid)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def secret(name):
    direct, filename = os.environ.get(name), os.environ.get(name + '_FILE')
    if direct is not None and filename:
        raise StartupError('conflicting_secret_sources', f'Set either {name} or {name}_FILE, not both.')
    if filename:
        try:
            return Path(filename).read_text(encoding='utf-8-sig').rstrip('\r\n')
        except (OSError, UnicodeError, ValueError):
            raise StartupError('secret_file_unreadable',
                               f'{name}_FILE must point to a readable UTF-8 file mounted inside the container.') from None
    return direct


def unprivileged(args, namespace=False):
    command = ['setpriv', '--reuid=transmission', '--regid=transmission', '--init-groups',
               '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--', *args]
    return ['ip', 'netns', 'exec', 'transmission-vpn', *command] if namespace else command


def spawn(args, env=None):
    process = subprocess.Popen(args, env=env)
    CHILDREN.append(process)
    return process


def initialize():
    if os.geteuid() != 0:
        raise StartupError('supervisor_user', 'Remove the container user override: the supervisor requires root; the torrent daemon drops privileges to UID 1000.')
    os.umask(0o077)
    for directory in (CONFIG, Path('/downloads')):
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, 1000, 1000)  # Never recursively chown downloaded data.
    settings = {k.replace('-', '_'): v for k, v in read(CONFIG / 'settings.json',
        read(ROOT / 'extras/auto/settings.json')).items()}
    password = secret('RPC_PASSWORD')
    if password is not None:
        if len(password) < 8 or any(c in password for c in '\r\n\x00'):
            raise StartupError('rpc_password_invalid', 'RPC_PASSWORD / RPC_PASSWORD_FILE must contain at least 8 characters and no line breaks.')
        settings['rpc_password'] = password
    elif not settings.get('rpc_password'):
        raise StartupError('rpc_password_missing', 'No RPC password is configured. Set RPC_PASSWORD_FILE or RPC_PASSWORD (minimum 8 characters) on first start.')
    elif not isinstance(settings['rpc_password'], str):
        raise StartupError('rpc_password_type', 'The rpc_password / rpc-password field in settings.json must be a string.')
    elif not settings['rpc_password'].startswith('{') and len(settings['rpc_password']) < 8:
        raise StartupError('rpc_password_invalid', 'The stored RPC password in settings.json must contain at least 8 characters.')
    settings.update(rpc_authentication_required=True, rpc_enabled=True,
                    rpc_username=os.environ.get('RPC_USERNAME', settings.get('rpc_username', 'transmission')),
                    rpc_port=19091, rpc_bind_address='127.0.0.1', rpc_whitelist_enabled=True,
                    rpc_whitelist='127.0.0.1', rpc_host_whitelist_enabled=False,
                    download_dir=os.environ.get('DOWNLOAD_DIR', settings.get('download_dir', '/downloads')))
    if not isinstance(settings['rpc_username'], str) or not settings['rpc_username']:
        raise StartupError('rpc_username_invalid', 'RPC_USERNAME / the stored RPC username must be a nonempty string.')
    write(CONFIG / 'settings.json', settings)
    vpn_enabled = os.environ.get('VPN_ENABLED', 'true').lower()
    if vpn_enabled not in ('true', 'false'):
        raise StartupError('vpn_enabled_invalid', 'VPN_ENABLED must be true or false.')
    enabled = vpn_enabled == 'true'
    if not enabled and ((CONFIG / 'vpn.json').exists() or any(k.startswith('TRANSMISSION_VPN_') for k in os.environ)):
        raise StartupError('vpn_disable_conflict', 'VPN_ENABLED=false conflicts with vpn.json or TRANSMISSION_VPN_* variables. Configured VPN protection cannot be silently disabled.')
    if enabled:
        sys.path.insert(0, str(ROOT / 'extras/vpn'))
        config = dict(provider='purevpn', openvpn_config='', credentials_file='',
                      dns=['1.1.1.1', '1.0.0.1'], subnet='10.203.74.0/30', start_timeout=90,
                      vpn_log_retention=256, vpn_log_flush_seconds=2)
        config.update(read(CONFIG / 'vpn.json'))
        # Deployment fields belong to the container, never to an imported host configuration.
        config.update(run_user='transmission', daemon=str(ROOT / 'bin/transmission-daemon'),
                      config_dir='/config', download_dir=settings['download_dir'], rpc_port=19091)
        write(CONFIG / 'vpn.json', config)
        # Container-owned paths override host-specific environment values as well.
        for key in ('run_user', 'daemon', 'config_dir', 'download_dir', 'rpc_port'):
            os.environ['TRANSMISSION_VPN_' + key.upper()] = str(config[key])
        for key in ('USERNAME', 'PASSWORD'):
            value = secret('TRANSMISSION_VPN_' + key)
            if value is not None:
                os.environ['TRANSMISSION_VPN_' + key] = value
        (CONFIG / 'vpn-status.json').unlink(missing_ok=True)
    tls = CONFIG / 'tls'
    tls.mkdir(mode=0o700, exist_ok=True)
    os.chmod(tls, 0o700)
    if not (tls / 'server.crt').exists() and not (tls / 'server.key').exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365',
                        '-subj', '/CN=transmission', '-addext', 'subjectAltName=DNS:localhost,DNS:transmission,IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:FALSE',
                        '-keyout', str(tls / 'server.key'), '-out', str(tls / 'server.crt')],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not (tls / 'server.crt').is_file() or not (tls / 'server.key').is_file():
        raise StartupError('tls_pair_missing', 'Both /config/tls/server.crt and /config/tls/server.key are required when supplying a custom TLS certificate.')
    os.chmod(tls / 'server.key', 0o600)
    # These control sockets need searchable parent directories despite the private umask.
    for directory in ('/run/transmission-vpn-test-control', '/run/transmission-rpc-security'):
        Path(directory).mkdir(mode=0o755, exist_ok=True)
        os.chmod(directory, 0o755)
    Path('/run/container-vpn-enabled').write_text('true' if enabled else 'false')
    return enabled


def main():
    global SECURITY
    enabled = initialize()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    clean_env = {k: v for k, v in os.environ.items() if not any(part in k for part in ('PASSWORD', 'TOKEN', 'SECRET'))}
    if enabled:
        spawn(['python3', str(ROOT / 'extras/vpn/controller.py'), 'run', '--config', '/config/vpn.json'])
        spawn(['python3', str(ROOT / 'extras/vpn/probe_service.py'), '--user', 'transmission'], clean_env)
    else:
        sys.path.insert(0, str(ROOT / 'extras/vpn'))
        from security_events import SecurityEvents
        SECURITY = SecurityEvents()
        spawn(unprivileged([str(ROOT / 'bin/transmission-daemon'), '--foreground', '--config-dir', '/config']), clean_env)
    spawn(['nginx', '-c', '/etc/transmission-rpc-tls/nginx.conf', '-g', 'daemon off;'], clean_env)
    admin = None
    security_entries = deque(maxlen=256)
    def security_event(source, message, level):
        security_entries.append(dict(time_ms=int(time.time() * 1000), source=source, message=message, level=level))
    deadline = time.monotonic() + 610
    print('Container starting: HTTPS :9091; managed VPN=' + str(enabled), flush=True)
    while not STOP.is_set():
        if SECURITY is not None:
            before = security_entries[-1] if security_entries else None
            SECURITY.poll(security_event)
            if security_entries and security_entries[-1] is not before:
                write(CONFIG / 'vpn-log.json', dict(entries=list(security_entries)))
        if any(child.poll() is not None for child in CHILDREN):
            raise RuntimeError('A supervised service exited; stopping container')
        if admin is None:
            ready = not enabled or read(CONFIG / 'vpn-status.json').get('state') == 'connected'
            if ready:
                admin = spawn(unprivileged(['python3', str(ROOT / 'mqtt/admin.py'), '--config-dir', '/config'], enabled))
            elif time.monotonic() > deadline:
                raise RuntimeError('VPN startup timed out')
        STOP.wait(2)
    return 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as error:
        # Configuration and credential contents must not enter Docker logs.
        print(failure_message(error), file=sys.stderr)
    finally:
        for child in reversed(CHILDREN):
            if child.poll() is None:
                child.terminate()
        for child in reversed(CHILDREN):
            try:
                child.wait(timeout=75)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if SECURITY is not None:
            SECURITY.close()
    sys.exit(code)
