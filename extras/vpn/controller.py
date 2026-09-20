#!/usr/bin/env python3
"""Linux VPN supervisor. The namespace firewall is the data-plane kill switch.

Provider adapters supply a declarative profile; they cannot replace firewall,
process supervision or credential handling. No polling of public IP services.
"""
import argparse
from collections import deque
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

from providers import PROVIDERS
from vpn_environment import resolve as resolve_environment

NS = 'transmission-vpn'
HOST_LINK = 'trvpn0'
TABLE = 'transmission_vpn'
RUNTIME = Path('/run/transmission-vpn')
RESOLVER = Path('/etc/netns') / NS
RELAY = Path(__file__).resolve().with_name('rpc-relay')


class SignalWakeup:
    """Wake select immediately on child exit or termination; no periodic PID polling."""
    def __init__(self):
        self.reader, self.writer = socket.socketpair()
        self.reader.setblocking(False)
        self.writer.setblocking(False)
        self.previous_fd = signal.set_wakeup_fd(self.writer.fileno())
        self.previous_handler = signal.signal(signal.SIGCHLD, lambda *_: None)

    def drain(self):
        try:
            while self.reader.recv(4096):
                pass
        except BlockingIOError:
            pass

    def close(self):
        signal.set_wakeup_fd(self.previous_fd)
        signal.signal(signal.SIGCHLD, self.previous_handler)
        self.reader.close()
        self.writer.close()


def command(*args, input=None, check=True):
    result = subprocess.run([str(x) for x in args], input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        # Never include generated configuration contents or credentials in errors.
        raise RuntimeError(f'{args[0]} failed: {result.stderr.strip()[:1000]}')
    return result


def secure_write(path, text, uid=0, gid=0):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            os.fchown(stream.fileno(), uid, gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_private_runtime():
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('/run/transmission-vpn must be a root-owned private directory')


def credentials(path, *, mounted_volume=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError('VPN credentials must be a regular private file (chmod 600), at most 4096 bytes')
        if info.st_mode & 0o077 and not mounted_volume:
            raise ValueError('VPN credentials must be a regular private file (chmod 600), at most 4096 bytes')
        lines = stream.read().splitlines()
    if len(lines) != 2 or any(not line or '\x00' in line for line in lines):
        raise ValueError('VPN credentials require exactly two nonempty lines: VPN username, VPN password')
    return '\n'.join(lines) + '\n'


def prepare_credentials(config, user, *, container_mode=False):
    """Validate credentials and stage container-mounted secrets in root-private tmpfs."""
    if config.get('username'):
        secret = config['username'] + '\n' + config['password'] + '\n'
    else:
        # DSM bind mounts can expose ACL-backed files as 0644/0666 in the
        # container. Only the root-owned container entrypoint may opt into
        # reading such a source; it is never passed directly to OpenVPN.
        secret = credentials(config['credentials_file'], mounted_volume=container_mode)
    if not container_mode:
        if config.get('username'):
            secure_write(config['credentials_file'], secret, user.pw_uid, user.pw_gid)
        return None
    ensure_private_runtime()
    staged = RUNTIME / 'credentials.input'
    secure_write(staged, secret)
    config['credentials_file'] = str(staged)
    return staged


def load_config(path, *, environ=None):
    config = json.loads(Path(path).read_text())
    if environ is not None:
        config = resolve_environment(config, environ)
    allowed = {'provider', 'openvpn_config', 'credentials_file', 'run_user', 'daemon',
               'config_dir', 'download_dir', 'dns', 'rpc_port', 'subnet', 'start_timeout',
               'openvpn_profile', 'username', 'password', 'vpn_log_retention', 'vpn_log_flush_seconds'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Unknown VPN configuration field')
    required = allowed - {'rpc_port', 'subnet', 'start_timeout', 'openvpn_profile', 'username', 'password',
                          'vpn_log_retention', 'vpn_log_flush_seconds'}
    if config.get('openvpn_profile'):
        if not isinstance(config['openvpn_profile'], str) or len(config['openvpn_profile'].encode()) > 60000:
            raise ValueError('Inline OpenVPN profile is invalid or too large')
        config['openvpn_config'] = str(Path(config.get('config_dir', '')) / 'vpn-managed.ovpn')
    if config.get('username') or config.get('password'):
        for name in ('username', 'password'):
            if not isinstance(config.get(name), str) or not config[name] or any(c in config[name] for c in '\r\n\x00'):
                raise ValueError('VPN username and password must be nonempty single-line values')
        config['credentials_file'] = str(Path(config.get('config_dir', '')) / 'vpn-credentials.txt')
        if len((config['username'] + '\n' + config['password'] + '\n').encode()) > 4096:
            raise ValueError('VPN credentials are too large')
    if required - set(config):
        raise ValueError('Missing VPN fields: ' + ', '.join(sorted(required - set(config))))
    if config['provider'] not in PROVIDERS:
        raise ValueError('Unknown VPN provider; use the providers command')
    user = pwd.getpwnam(config['run_user'])
    if user.pw_uid == 0:
        raise ValueError('Transmission must run as a non-root user for the kill switch')
    for field in ('openvpn_config', 'credentials_file', 'daemon', 'config_dir', 'download_dir'):
        value = config[field]
        if not isinstance(value, str) or not Path(value).is_absolute() or any(c in value for c in '\n\r\x00'):
            raise ValueError(f'{field} must be an absolute path')
        config[field] = str(Path(value) if field == 'credentials_file' else Path(value).resolve())
    network = ipaddress.IPv4Network(config.get('subnet', '10.203.74.0/30'))
    private = [ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    if network.prefixlen != 30 or not any(network.subnet_of(n) for n in private):
        raise ValueError('VPN underlay must be a private RFC1918 /30 subnet')
    config['subnet'] = str(network)
    config['host_ip'], config['guest_ip'] = map(str, network.hosts())
    for field, default, low, high in [('rpc_port', 9091, 1024, 65535), ('start_timeout', 90, 5, 600)]:
        value = config.get(field, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Invalid {field}')
        config[field] = value
    for field, default, low, high in [('vpn_log_retention', 256, 1, 2000),
                                      ('vpn_log_flush_seconds', 2, 1, 30)]:
        value = config.get(field, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Invalid {field}')
        config[field] = value
    if not isinstance(config['dns'], list) or not 1 <= len(config['dns']) <= 3:
        raise ValueError('Specify one to three IPv4 DNS servers, reached only through the tunnel')
    config['dns'] = [str(ipaddress.IPv4Address(ip)) for ip in config['dns']]
    for ip in config['dns']:
        address = ipaddress.IPv4Address(ip)
        if address.is_loopback or address.is_unspecified or address.is_multicast or address in network:
            raise ValueError('DNS cannot use loopback or the VPN underlay')
    return config, user


def namespace_rules(config, profile, endpoint):
    endpoint = str(ipaddress.IPv4Address(endpoint))
    host, port = config['host_ip'], config['rpc_port']
    transport = f'{profile.protocol} dport {profile.port}'
    incoming = f'{profile.protocol} sport {profile.port}'
    return f'''table inet {TABLE} {{
 chain output {{
  type filter hook output priority 0; policy drop;
  oifname "lo" accept
  meta nfproto ipv6 counter drop
  oifname "tun0" accept
  oifname "uplink" meta skuid 0 ip daddr {endpoint} {transport} accept
  oifname "uplink" ip daddr {host} tcp sport {port} ct state established accept
  counter drop
 }}
 chain input {{
  type filter hook input priority 0; policy drop;
  iifname "lo" accept
  meta nfproto ipv6 counter drop
  iifname "tun0" tcp dport {port} counter drop
  iifname "tun0" accept
  iifname "uplink" ip saddr {endpoint} {incoming} ct state established accept
  iifname "uplink" ip saddr {host} tcp dport {port} accept
  iifname "uplink" ip protocol icmp ct state related accept
  counter drop
 }}
 chain forward {{ type filter hook forward priority 0; policy drop; }}
}}
'''


def nat_rules(config, profile, endpoint):
    return f'''table ip {TABLE} {{
 chain postrouting {{
  type nat hook postrouting priority srcnat; policy accept;
  ip saddr {config['guest_ip']} ip daddr {endpoint} {profile.protocol} dport {profile.port} masquerade
 }}
}}
'''


class Supervisor:
    def __init__(self, config, user, profile, *, probe=False):
        self.probe = probe
        self.config, self.user, self.profile = config, user, profile
        self.children = []
        self.stopping = False
        self.owned_ns = self.owned_link = self.owned_nat = self.owned_resolver = False
        self.lock = None
        self.last_state = None
        self.vpn_history = deque()
        self.vpn_history_dirty = False
        self.vpn_history_bytes = 0
        self.vpn_log_offset = 0
        self.vpn_log_partial = b''
        self.next_public_log_check = 0
        self.redactions = set()
        self.failure_message = None
        self.failed = False
        self.next_log_check = 0
        self.log_paths = tuple(RUNTIME / name for name in ('openvpn.log', 'transmission.log', 'rpc.log'))
        self.wakeup = None
        self.security_events = None
        # These are deliberately limited to connection metadata. The rendered
        # OpenVPN profile and credentials never leave the private runtime area.
        self.server_endpoint = None
        self.tunnel_ipv4 = None

    def public_status(self, state):
        status = {
            'provider': self.config['provider'], 'state': state,
            'protocol': self.profile.protocol, 'kill_switch': 'namespace-firewall',
            'time': int(time.time()),
        }
        if self.server_endpoint is not None:
            status.update(server_hostname=self.profile.hostname,
                          server_endpoint=self.server_endpoint,
                          server_port=self.profile.port)
        if self.tunnel_ipv4 is not None:
            status['tunnel_ipv4'] = self.tunnel_ipv4
        return status

    def capture_tunnel_ipv4(self):
        addresses = json.loads(command('ip', '-n', NS, '-j', '-4', 'addr', 'show', 'dev', 'tun0').stdout)
        for interface in addresses:
            for address in interface.get('addr_info', []):
                if address.get('family') == 'inet' and address.get('local'):
                    self.tunnel_ipv4 = str(ipaddress.IPv4Address(address['local']))
                    return self.tunnel_ipv4
        return None

    def event(self, state, message=None, level=4):
        if state == self.last_state:
            return
        self.last_state = state
        secure_write(RUNTIME / 'status.json', json.dumps({
            'provider': self.config['provider'], 'state': state, 'pid': os.getpid(),
            'rpc': f"http://127.0.0.1:{self.config['rpc_port']}",
            'kill_switch': 'namespace-firewall', 'time': int(time.time())}) + '\n')
        # Public-to-the-daemon metadata only; credentials and management socket
        # remain inside the root-private runtime directory. Write on transitions.
        if not self.probe:
            # A bounded, transition-only public VPN log. It never exposes the
            # OpenVPN profile, credentials or root-private runtime log.
            text = message or {'starting': 'VPN supervisor starting',
                               'connecting': 'Connecting to VPN',
                               'connected': 'VPN tunnel connected',
                               'reconnecting': 'VPN reconnecting',
                               'stopped': 'VPN supervisor stopped',
                               'failed': 'VPN supervisor failed'}.get(state, state)
            self.add_public_log('VPN', text, level)
            self.persist_public_log()
        if getattr(self, 'settings_ready', False):
            secure_write(Path(self.config['config_dir']) / 'vpn-status.json',
                         json.dumps(self.public_status(state), separators=(',', ':')) + '\n',
                         self.user.pw_uid, self.user.pw_gid)
        print(f'VPN: {state}', flush=True)

    def redact_log(self, message):
        message = str(message)
        for secret in self.redactions:
            if secret:
                message = message.replace(secret, '***')
        message = re.sub(r'(?i)(\\b(?:password|passwd|token|secret|username|user)\\b\\s*[:=]\\s*)[^,\\s]+', r'\\1***', message)
        message = re.sub(r'(?i)(://[^:/\\s]+:)[^@/\\s]+@', r'\\1***@', message)
        return message

    def add_public_log(self, source, message, level=4):
        message = self.redact_log(message)
        encoded = message.encode('utf-8', errors='replace')
        if len(encoded) > 4096:
            encoded = encoded[:4093]
            while encoded and (encoded[-1] & 0xc0) == 0x80:
                encoded = encoded[:-1]
            message = encoded.decode('utf-8', errors='replace') + '…'
        entry = {'time_ms': int(time.time() * 1000), 'level': level, 'source': source, 'message': message}
        serialized = json.dumps(entry, separators=(',', ':'))
        self.vpn_history.append(serialized)
        self.vpn_history_dirty = True
        self.vpn_history_bytes += len(serialized)
        while self.vpn_history and (len(self.vpn_history) > self.config['vpn_log_retention'] or self.vpn_history_bytes > 60000):
            self.vpn_history_bytes -= len(self.vpn_history.popleft())

    def persist_public_log(self):
        if not self.probe and self.vpn_history_dirty:
            secure_write(Path(self.config['config_dir']) / 'vpn-log.json',
                         '{"entries":[' + ','.join(self.vpn_history) + ']}\n',
                         self.user.pw_uid, self.user.pw_gid)
            self.vpn_history_dirty = False

    def sync_public_log(self, force=False):
        if self.probe:
            return
        now = time.monotonic()
        if not force and now < self.next_public_log_check:
            return
        self.next_public_log_check = now + self.config['vpn_log_flush_seconds']
        if self.security_events is not None:
            self.security_events.poll(self.add_public_log, force=force)
            self.persist_public_log()
        path = RUNTIME / 'openvpn.log'
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size < self.vpn_log_offset:
            self.vpn_log_offset = 0
            self.vpn_log_partial = b''
        if size == self.vpn_log_offset:
            return
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.lseek(fd, self.vpn_log_offset, os.SEEK_SET)
            chunk = os.read(fd, min(65536, size - self.vpn_log_offset))
        finally:
            os.close(fd)
        self.vpn_log_offset += len(chunk)
        lines = (self.vpn_log_partial + chunk).split(b'\n')
        self.vpn_log_partial = lines.pop()
        changed = False
        for raw in lines:
            line = raw.decode('utf-8', errors='replace').rstrip('\r')
            if not line:
                continue
            level = 2 if re.search(r'(?i)\\b(error|failed|fatal|auth_failed)\\b', line) else 3 if re.search(r'(?i)\\b(warn|retry|reconnect)\\b', line) else 4
            self.add_public_log('OpenVPN', line, level)
            changed = True
        if changed:
            self.persist_public_log()

    def preflight(self):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise RuntimeError('VPN run requires Linux and root; Transmission itself runs unprivileged')
        for tool in ('ip', 'nft', 'openvpn', 'setpriv'):
            if not shutil.which(tool):
                raise RuntimeError(f'Missing dependency: {tool}')
        if not self.probe and (not RELAY.is_file() or not os.access(RELAY, os.X_OK)):
            raise RuntimeError('Missing rpc-relay executable; run extras/vpn/build-relay.sh or use the binary package')
        if not Path('/dev/net/tun').is_char_device():
            raise RuntimeError('/dev/net/tun is unavailable')
        if Path('/proc/sys/net/ipv4/ip_forward').read_text().strip() != '1':
            raise RuntimeError('IPv4 forwarding is disabled; administrator setup: sysctl -w net.ipv4.ip_forward=1')
        daemon = Path(self.config['daemon'])
        if not self.probe and (not daemon.is_file() or not os.access(daemon, os.X_OK)):
            raise RuntimeError('Transmission daemon is not executable')
        ensure_private_runtime()
        self.lock = os.open(RUNTIME / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another VPN supervisor is already running') from None
        namespaces = [row['name'] for row in json.loads(command('ip', '-j', 'netns', 'list').stdout or '[]')]
        if NS in namespaces or RESOLVER.exists() or RESOLVER.is_symlink():
            raise RuntimeError('VPN namespace/resolver already exists; inspect stale resources before restarting')
        if command('ip', 'link', 'show', HOST_LINK, check=False).returncode == 0:
            raise RuntimeError('VPN host interface already exists')
        if command('nft', 'list', 'table', 'ip', TABLE, check=False).returncode == 0:
            raise RuntimeError('VPN NAT table already exists')
        network = ipaddress.IPv4Network(self.config['subnet'])
        for route in json.loads(command('ip', '-j', '-4', 'route', 'show', 'table', 'all').stdout):
            destination = route.get('dst', 'default')
            if destination != 'default' and network.overlaps(ipaddress.IPv4Network(destination, strict=False)):
                raise RuntimeError('VPN underlay overlaps an existing route; choose another private /30 subnet')
        # Reserve/check the public control endpoint before creating any network state.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if not self.probe:
                probe.bind(('127.0.0.1', self.config['rpc_port']))

    def network(self, endpoint):
        c = self.config
        command('ip', 'netns', 'add', NS)
        self.owned_ns = True
        # Firewall is installed before the underlay link or any service is started.
        command('ip', 'netns', 'exec', NS, 'nft', '-f', '-', input=namespace_rules(c, self.profile, endpoint))
        command('ip', 'link', 'add', HOST_LINK, 'type', 'veth', 'peer', 'name', 'uplink', 'netns', NS)
        self.owned_link = True
        command('ip', 'addr', 'add', c['host_ip'] + '/30', 'dev', HOST_LINK)
        command('ip', 'link', 'set', HOST_LINK, 'up')
        command('ip', '-n', NS, 'addr', 'add', c['guest_ip'] + '/30', 'dev', 'uplink')
        command('ip', '-n', NS, 'link', 'set', 'lo', 'up')
        command('ip', '-n', NS, 'link', 'set', 'uplink', 'up')
        command('ip', '-n', NS, 'route', 'add', 'default', 'via', c['host_ip'])
        command('nft', '-f', '-', input=nat_rules(c, self.profile, endpoint))
        self.owned_nat = True
        RESOLVER.mkdir(mode=0o755, parents=True)
        self.owned_resolver = True
        # ip netns exec creates a mount namespace for this resolver override.
        (RESOLVER / 'resolv.conf').write_text(''.join('nameserver ' + ip + '\n' for ip in c['dns']))
        (RESOLVER / 'resolv.conf').chmod(0o644)

    def settings(self):
        c, user = self.config, self.user
        for field in ('config_dir', 'download_dir'):
            path = Path(c[field])
            if not path.exists():
                path.mkdir(mode=0o700, parents=True)
                os.chown(path, user.pw_uid, user.pw_gid)
            if path.stat().st_uid != user.pw_uid:
                raise RuntimeError(f'{field} must be owned by {user.pw_name}; existing ownership is not changed')
        path = Path(c['config_dir']) / 'settings.json'
        if path.exists():
            settings = {key.replace('-', '_'): value for key, value in json.loads(path.read_text()).items()}
        else:
            settings = json.loads((Path(__file__).resolve().parent.parent / 'auto/settings.json').read_text())
        # All tracker, DHT, peer, webseed and DNS sockets share the same namespace.
        settings.update(rpc_enabled=True, rpc_bind_address='0.0.0.0', rpc_port=c['rpc_port'],
                        rpc_whitelist_enabled=True, rpc_whitelist=c['host_ip'] + ',127.0.0.1',
                        port_forwarding_enabled=False, lpd_enabled=False,
                        bind_address_ipv4='0.0.0.0', download_dir=c['download_dir'])
        secure_write(path, json.dumps(settings, indent=2) + '\n', user.pw_uid, user.pw_gid)
        self.settings_ready = True

    def spawn(self, args, log, unprivileged=False, in_namespace=False):
        if unprivileged:
            args = ['setpriv', f'--reuid={self.user.pw_uid}', f'--regid={self.user.pw_gid}',
                    '--init-groups', '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all',
                    '--ambient-caps=-all', '--', *args]
        if in_namespace:
            args = ['ip', 'netns', 'exec', NS, *args]
        env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': self.user.pw_dir, 'LANG': 'C.UTF-8'}
        if unprivileged and in_namespace:
            env['TRANSMISSION_VPN_STATUS_FILE'] = str(Path(self.config['config_dir']) / 'vpn-status.json')
        web = Path(self.config['daemon']).parent.parent / 'share/transmission/public_html'
        if web.is_dir():
            env['TRANSMISSION_WEB_HOME'] = str(web)
        fd = os.open(RUNTIME / log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     env=env, start_new_session=True)
        self.children.append(child)
        return child

    def trim_logs(self):
        now = time.monotonic()
        if now < self.next_log_check:
            return
        self.next_log_check = now + 30
        scratch = None
        for path in self.log_paths:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if size > 1024 * 1024:
                # Writers use O_APPEND, so truncation cannot create sparse holes.
                # Reuse a small buffer for every log; source/destination do not overlap.
                if scratch is None:
                    scratch = bytearray(64 * 1024)
                with path.open('r+b', buffering=0) as stream:
                    stream.seek(-256 * 1024, os.SEEK_END)
                    source = stream.tell()
                    destination = 0
                    while destination < 256 * 1024:
                        stream.seek(source + destination)
                        count = stream.readinto(memoryview(scratch)[:min(len(scratch), 256 * 1024 - destination)])
                        if not count:
                            break
                        stream.seek(destination)
                        view = memoryview(scratch)[:count]
                        while view:
                            written = stream.write(view)
                            if not written:
                                raise OSError('Short write while trimming VPN log')
                            view = view[written:]
                        destination += count
                    stream.truncate(destination)

    def run(self):
        self.preflight()
        try:
            if not self.probe and Path('/etc/transmission-rpc-tls/nginx.conf').is_file():
                from security_events import SecurityEvents
                self.security_events = SecurityEvents()
            self.event('starting')
            secret = credentials(self.config['credentials_file'])
            self.redactions = set(secret.splitlines())
            endpoint = self.profile.resolve()  # Only provider bootstrap DNS uses the host resolver.
            self.server_endpoint = endpoint
            if endpoint in self.config['dns']:
                raise ValueError('VPN endpoint cannot also be used as a DNS server')
            secure_write(RUNTIME / 'auth.txt', secret)
            del secret
            management_path = RUNTIME / 'management.sock'
            management_path.unlink(missing_ok=True)
            secure_write(RUNTIME / 'client.ovpn', self.profile.render(endpoint, RUNTIME / 'auth.txt', management_path))
            if not self.probe:
                self.settings()
            self.network(endpoint)
            self.wakeup = SignalWakeup()
            vpn = self.spawn(['openvpn', '--config', str(RUNTIME / 'client.ovpn')], 'openvpn.log', in_namespace=True)
            deadline = time.monotonic() + self.config['start_timeout']
            self.event('connecting')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as management:
                while not self.stopping:
                    if vpn.poll() is not None:
                        raise RuntimeError('OpenVPN exited before connection; inspect the private OpenVPN log')
                    try:
                        management.connect(str(management_path))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('OpenVPN startup timed out')
                        time.sleep(0.1)
                if self.stopping:
                    return
                management.sendall(b'state on\nstate\n')
                buffer = b''
                daemon = proxy = None
                while not self.stopping:
                    self.trim_logs()
                    self.sync_public_log()
                    if vpn.poll() is not None:
                        raise RuntimeError('OpenVPN stopped; Transmission is being shut down')
                    if daemon is not None and (daemon.poll() is not None or proxy.poll() is not None):
                        raise RuntimeError('Transmission or the local RPC proxy stopped')
                    if daemon is None and time.monotonic() >= deadline:
                        raise RuntimeError('VPN connection timed out; Transmission was not started')
                    timeout = min(max(0, self.next_log_check - time.monotonic()),
                                  max(0, self.next_public_log_check - time.monotonic()))
                    if daemon is None:
                        timeout = min(timeout, max(0, deadline - time.monotonic()))
                    ready = select.select([management, self.wakeup.reader], [], [], timeout)[0]
                    if self.wakeup.reader in ready:
                        self.wakeup.drain()
                    if management not in ready:
                        continue
                    chunk = management.recv(8192)
                    if not chunk:
                        raise RuntimeError('VPN management connection closed')
                    buffer += chunk
                    if len(buffer) > 65536:
                        raise RuntimeError('Invalid VPN management response')
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        line = line.decode('utf-8', errors='replace').strip().removeprefix('>STATE:')
                        fields = line.split(',')
                        if len(fields) < 3 or not fields[0].isdigit():
                            continue
                        state = fields[1]
                        if state == 'CONNECTED' and fields[2] == 'SUCCESS':
                            tunnel_ipv4 = self.capture_tunnel_ipv4()
                            if tunnel_ipv4 is None:
                                raise RuntimeError('VPN tunnel has no IPv4 address')
                            if self.probe:
                                self.event('connected')
                                return True
                            if daemon is None:
                                daemon = self.spawn([self.config['daemon'], '--foreground', '--config-dir',
                                                     self.config['config_dir']], 'transmission.log', True, True)
                                c = self.config
                                proxy = self.spawn([str(RELAY), str(c['rpc_port']), c['guest_ip'],
                                                    str(c['rpc_port'])], 'rpc.log', True)
                            self.event('connected')
                        elif state in ('RECONNECTING', 'WAIT', 'AUTH', 'GET_CONFIG', 'ASSIGN_IP', 'ADD_ROUTES'):
                            self.event('reconnecting' if daemon else 'connecting')
        except BaseException as exc:
            self.failed = True
            self.failure_message = str(exc)
            raise
        finally:
            self.cleanup()

    def cleanup(self):
        # Keep the firewall and VPN alive until Transmission has flushed its cache.
        self.sync_public_log(force=True)
        if self.security_events is not None:
            self.security_events.close()
            self.security_events = None
        for child in reversed(self.children):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if child.poll() is None:
                try:
                    child.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        if self.wakeup is not None:
            self.wakeup.close()
            self.wakeup = None
        if self.owned_link:
            command('ip', 'link', 'delete', HOST_LINK, check=False)
        if self.owned_nat:
            command('nft', 'delete', 'table', 'ip', TABLE, check=False)
        if self.owned_ns:
            command('ip', 'netns', 'delete', NS, check=False)
        if self.owned_resolver:
            (RESOLVER / 'resolv.conf').unlink(missing_ok=True)
            RESOLVER.rmdir()
        for name in ('auth.txt', 'credentials.input', 'client.ovpn', 'management.sock'):
            (RUNTIME / name).unlink(missing_ok=True)
        if self.last_state is not None:
            self.event('failed' if self.failed else 'stopped', self.failure_message,
                       2 if self.failed else 4)
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['providers', 'check', 'run', 'status'])
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    supervisor = None
    staged_credentials = None
    try:
        if args.action == 'providers':
            for name, provider in PROVIDERS.items():
                print(f'{name}: {provider.name} / {provider.protocol}. {provider.credentials_help}')
            return 0
        if args.action == 'status':
            print((RUNTIME / 'status.json').read_text() if (RUNTIME / 'status.json').exists() else '{"state":"not-running"}')
            return 0
        if args.config is None:
            parser.error('--config is required')
        config, user = load_config(args.config, environ=os.environ)
        if config.get('openvpn_profile'):
            secure_write(config['openvpn_config'], config['openvpn_profile'], user.pw_uid, user.pw_gid)
        profile = PROVIDERS[config['provider']].load(config['openvpn_config'])
        container_marker = Path('/run/container-vpn-enabled')
        container_mode = container_marker.is_file() and container_marker.read_text().strip() == 'true'
        staged_credentials = prepare_credentials(config, user, container_mode=container_mode)
        credentials(config['credentials_file'])
        if args.action == 'check':
            print(f"Valid {config['provider']} profile, {profile.protocol.upper()} transport; no connection attempted.")
            return 0
        supervisor = Supervisor(config, user, profile)
        def stop(_signum, _frame):
            supervisor.stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        supervisor.run()
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f'VPN error: {exc}', file=sys.stderr)
        return 1
    finally:
        if staged_credentials is not None:
            staged_credentials.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
