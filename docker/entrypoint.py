"""Zero-configuration startup; configured VPN connections always fail closed."""
from collections import deque
import json
import os
from pathlib import Path
import signal
import secrets
import stat
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
    direct, filename = os.environ.get(name) or None, os.environ.get(name + '_FILE') or None
    if direct is not None and filename:
        raise StartupError('conflicting_secret_sources', f'Set either {name} or {name}_FILE, not both.')
    if filename:
        try:
            return Path(filename).read_text(encoding='utf-8-sig').rstrip('\r\n')
        except (OSError, UnicodeError, ValueError):
            raise StartupError('secret_file_unreadable',
                               f'{name}_FILE must point to a readable UTF-8 file mounted inside the container.') from None
    return direct


def initial_password(settings):
    """Recover the same bootstrap secret after interrupted first startup."""
    path = CONFIG / 'rpc-initial-password.txt'
    if path.exists():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or info.st_mode & 0o077:
                raise StartupError('initial_password_permissions', 'rpc-initial-password.txt must be a private regular file (mode 600), at most 4096 bytes.')
            password = stream.read().rstrip('\r\n')
        if len(password) < 8 or any(c in password for c in '\r\n\x00'):
            raise StartupError('initial_password_invalid', 'The existing rpc-initial-password.txt is invalid. Supply RPC_PASSWORD or repair the initial password file.')
    else:
        password = secrets.token_urlsafe(24)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(password + '\n')
            os.fchown(stream.fileno(), 1000, 1000)
    settings['rpc_password'] = password
    print('RPC initial password generated/preserved in /config/rpc-initial-password.txt; username defaults to transmission. Change it in Settings after login.', flush=True)


def disk_profile_rules():
    """Validate persistent per-directory policy and expose a compact daemon rule set."""
    path = CONFIG / 'storage-profiles.json'
    config = read(path) if path.exists() else {}
    default = os.environ.get('STORAGE_PROFILE_DEFAULT', config.get('default', 'auto'))
    directories = config.get('directories', {})
    if not isinstance(default, str) or default.lower() not in ('auto', 'hdd', 'ssd'):
        raise StartupError('storage_profile_default_invalid',
                           'STORAGE_PROFILE_DEFAULT / storage-profiles.json default must be auto, hdd or ssd.')
    if not isinstance(directories, dict):
        raise StartupError('storage_profile_directories_invalid',
                           'storage-profiles.json directories must be an object of absolute container paths.')
    rules = []
    for directory, kind in directories.items():
        if (not isinstance(directory, str) or not directory.startswith('/') or ';' in directory or '=' in directory or
                not isinstance(kind, str) or kind.lower() not in ('auto', 'hdd', 'ssd')):
            raise StartupError('storage_profile_rule_invalid',
                               'Storage profile rules require absolute paths and auto, hdd or ssd values.')
        if kind.lower() != 'auto':
            rules.append(f'{directory}={kind.lower()}')
    explicit = os.environ.get('STORAGE_PROFILE_RULES')
    if explicit:
        for item in explicit.split(';'):
            directory, separator, kind = item.rpartition('=')
            if (not separator or not directory.startswith('/') or ';' in directory or
                    kind.lower() not in ('hdd', 'ssd')):
                raise StartupError('storage_profile_rules_invalid',
                                   'STORAGE_PROFILE_RULES must look like /downloads=hdd;/movies=ssd.')
        rules = explicit.split(';')
    if default.lower() != 'auto':
        rules.append(f'*={default.lower()}')
    return ';'.join(rules)


def vpn_requested(config, environ=None):
    env = os.environ if environ is None else environ
    mode = (env.get('VPN_ENABLED') or 'auto').strip().lower()
    if mode not in ('auto', 'true', 'false'):
        raise StartupError('vpn_enabled_invalid', 'VPN_ENABLED must be auto, true or false; omitted means auto.')
    if mode != 'auto':
        return mode == 'true'
    # Old images wrote a defaults-only vpn.json before failing. Such a stub
    # is not a configured VPN and must not prevent zero-configuration startup.
    fields = ('openvpn_config', 'openvpn_profile', 'credentials_file', 'username', 'password')
    return any(config.get(key) for key in fields) or any(
        env.get('TRANSMISSION_VPN_' + key.upper()) for key in (*fields, 'username_file', 'password_file'))


def vpn_probe_available():
    if not Path('/dev/net/tun').is_char_device():
        return False
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('CapEff:'):
            capabilities = int(line.split()[1], 16)
            required = (1 << 12) | (1 << 21)  # NET_ADMIN and SYS_ADMIN
            return capabilities & required == required
    return False


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
        initial_password(settings)
    elif not isinstance(settings['rpc_password'], str):
        raise StartupError('rpc_password_type', 'The rpc_password / rpc-password field in settings.json must be a string.')
    elif not settings['rpc_password'].startswith('{') and len(settings['rpc_password']) < 8:
        raise StartupError('rpc_password_invalid', 'The stored RPC password in settings.json must contain at least 8 characters.')
    settings.update(rpc_authentication_required=True, rpc_enabled=True,
                    rpc_username=os.environ.get('RPC_USERNAME') or settings.get('rpc_username') or 'transmission',
                    rpc_port=19091, rpc_bind_address='127.0.0.1', rpc_whitelist_enabled=True,
                    rpc_whitelist='127.0.0.1', rpc_host_whitelist_enabled=False,
                    download_dir=os.environ.get('DOWNLOAD_DIR') or settings.get('download_dir') or '/downloads')
    rules = disk_profile_rules()
    if rules:
        os.environ['TRANSMISSION_DISK_PROFILE_RULES'] = rules
    if not isinstance(settings['rpc_username'], str) or not settings['rpc_username']:
        raise StartupError('rpc_username_invalid', 'RPC_USERNAME / the stored RPC username must be a nonempty string.')
    write(CONFIG / 'settings.json', settings)
    stored_vpn = read(CONFIG / 'vpn.json')
    enabled = vpn_requested(stored_vpn)
    if enabled:
        sys.path.insert(0, str(ROOT / 'extras/vpn'))
        config = dict(provider='purevpn', openvpn_config='', credentials_file='',
                      dns=['1.1.1.1', '1.0.0.1'], subnet='10.203.74.0/30', start_timeout=90,
                      vpn_log_retention=256, vpn_log_flush_seconds=2)
        config.update(stored_vpn)
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
        from vpn_environment import resolve
        effective = resolve(config)
        if not (effective.get('openvpn_config') or effective.get('openvpn_profile')):
            raise StartupError('vpn_profile_missing', 'VPN is configured/enabled but no profile is provided. Set TRANSMISSION_VPN_OPENVPN_CONFIG or save a profile in vpn.json. No direct-network fallback was started.')
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
    else:
        sys.path.insert(0, str(ROOT / 'extras/vpn'))
        from security_events import SecurityEvents
        SECURITY = SecurityEvents()
        spawn(unprivileged([str(ROOT / 'bin/transmission-daemon'), '--foreground', '--config-dir', '/config']), clean_env)
    probe_available = enabled or vpn_probe_available()
    if probe_available:
        spawn(['python3', str(ROOT / 'extras/vpn/probe_service.py'), '--user', 'transmission'], clean_env)
    else:
        print('VPN connection test disabled: recreate the container with /dev/net/tun, '
              'NET_ADMIN, SYS_ADMIN, net.ipv4.ip_forward=1 and DSM AppArmor unconfined. '
              'No separate transmission-vpn-test container is required.', flush=True)
    spawn(['nginx', '-c', '/etc/transmission-rpc-tls/nginx.conf', '-g', 'daemon off;'], clean_env)
    admin = None
    security_entries = deque(maxlen=256)
    def security_event(source, message, level):
        security_entries.append(dict(time_ms=int(time.time() * 1000), source=source, message=message, level=level))
    deadline = time.monotonic() + 610
    print('Container starting: HTTPS :9091; DSM HTTP backend :9092; managed VPN=' + str(enabled), flush=True)
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
