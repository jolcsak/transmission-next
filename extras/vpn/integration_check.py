"""Run only in isolated mount AND network namespaces (see run-vpn-tests.sh)."""
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

assert os.geteuid() == 0
assert os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net')
assert os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt')
source = Path(sys.argv[1]).resolve()
daemon = Path(sys.argv[2]).resolve()
out = Path(sys.argv[3]).resolve()
transport = sys.argv[4] if len(sys.argv) > 4 else 'udp'
assert transport in ('udp', 'tcp')
out.mkdir(mode=0o755, exist_ok=False)
sys.path.insert(0, str(source))
import controller
from providers.purevpn import PureVPN

def cmd(*args, **kwargs):
    result = subprocess.run(list(map(str, args)), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, **kwargs)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout

def wait_for(test, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if test():
            return
        time.sleep(.1)
    raise AssertionError('Timed out waiting for condition')

def ns_user(code):
    return cmd('ip', 'netns', 'exec', controller.NS, 'setpriv', '--reuid=65534', '--regid=65534',
               '--clear-groups', '--no-new-privs', '--bounding-set=-all', 'python3', '-c', code)

cmd('ip', 'link', 'set', 'lo', 'up')
cmd('ip', 'addr', 'add', '192.0.2.2/32', 'dev', 'lo')
cmd('ip', 'addr', 'add', '198.18.0.1/32', 'dev', 'lo')
cmd('sysctl', '-w', 'net.ipv4.ip_forward=1')

# Locally generated test CA and TLS server. No PureVPN or Internet connection.
cmd('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=VPN-Test-CA',
    '-keyout', out/'ca.key', '-out', out/'ca.crt')
cmd('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=VPN-Test-Server',
    '-keyout', out/'server.key', '-out', out/'server.csr')
(out/'extensions').write_text('extendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n')
cmd('openssl', 'x509', '-req', '-in', out/'server.csr', '-CA', out/'ca.crt', '-CAkey', out/'ca.key',
    '-CAcreateserial', '-days', '1', '-extfile', out/'extensions', '-out', out/'server.crt')
(out/'auth.sh').write_text('#!/bin/sh\nread -r name < "$1"\n[ "$name" != probe-rejected ]\n')
(out/'auth.sh').chmod(0o700)
server_config = f'''local 192.0.2.2
port 11940
proto {'udp4' if transport == 'udp' else 'tcp4-server'}
dev server0
dev-type tun
topology subnet
server 10.44.0.0 255.255.255.0
ca {out}/ca.crt
cert {out}/server.crt
key {out}/server.key
dh none
verify-client-cert none
username-as-common-name
auth-user-pass-verify {out}/auth.sh via-file
script-security 2
keepalive 15 60
data-ciphers AES-256-GCM
compress
verb 3
'''
(out/'server.conf').write_text(server_config)
if os.environ.get('VPN_BENCH_KEEPALIVE') == '1':
    # Isolate the client's fallback policy: no server-pushed ping override.
    (out/'server.conf').write_text(server_config.replace('keepalive 15 60', 'ping 15\nping-restart 120'))
(out/'client.ovpn').write_text(f'client\nproto {transport}\nremote 192.0.2.2 11940\nca ca.crt\n'
    'cipher AES-256-GCM\ncompress\nroute-method exe\nroute 0.0.0.0 0.0.0.0\nscript-security 2\n')
if os.environ.get('VPN_TEST_NO_COMPRESSION') == '1':
    for filename in ('server.conf', 'client.ovpn'):
        path = out / filename
        path.write_text(path.read_text().replace('compress\n', ''))
(out/'auth.txt').write_text('fixture-user\nfixture-password\n')
(out/'auth.txt').chmod(0o600)
config = dict(provider='purevpn', openvpn_config=str(out/'client.ovpn'), credentials_file=str(out/'auth.txt'),
              run_user='nobody', daemon=str(daemon), config_dir=str(out/'config'), download_dir=str(out/'downloads'),
              dns=['198.18.0.1'], rpc_port=19091, start_timeout=15)
(out/'vpn.json').write_text(json.dumps(config))
c, user = controller.load_config(out/'vpn.json')
profile = PureVPN.load(out/'client.ovpn')
results = {'transport':transport}

# Actual kernel firewall, before any VPN tunnel exists.
supervisor = controller.Supervisor(c, user, profile)
supervisor.preflight()
try:
    supervisor.network('192.0.2.2')
    canary = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    canary.bind(('198.18.0.1', 53))
    canary.settimeout(.5)
    ns_user("import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\ntry: s.sendto(b'no-dns-leak',('198.18.0.1',53))\nexcept OSError: pass")
    try:
        canary.recvfrom(1024)
        raise AssertionError('Plaintext DNS escaped before VPN startup')
    except socket.timeout:
        results['dns_blocked_before_vpn'] = True
    canary.close()
    canary = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if transport == 'udp' else socket.SOCK_STREAM)
    canary.bind(('192.0.2.2', 11940))
    if transport == 'tcp':
        canary.listen(1)
    canary.settimeout(.5)
    if transport == 'udp':
        ns_user("import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\ntry: s.sendto(b'not-openvpn',('192.0.2.2',11940))\nexcept OSError: pass")
    else:
        ns_user("import socket\ntry: socket.create_connection(('192.0.2.2',11940),.5)\nexcept OSError: pass")
    try:
        canary.recvfrom(1024) if transport == 'udp' else canary.accept()
        raise AssertionError('Non-root process bypassed endpoint rule')
    except socket.timeout:
        results['endpoint_exception_root_only'] = True
    canary.close()
    # IPv6 has a deliberately usable route: filtering, not missing routes, blocks it.
    cmd('ip', 'addr', 'add', 'fd44::1/64', 'dev', controller.HOST_LINK)
    cmd('ip', '-n', controller.NS, '-6', 'addr', 'add', 'fd44::2/64', 'dev', 'uplink', 'nodad')
    cmd('ip', '-n', controller.NS, '-6', 'neigh', 'add', 'fd44::1', 'lladdr',
        json.loads(cmd('ip', '-j', 'link', 'show', controller.HOST_LINK))[0]['address'], 'dev', 'uplink')
    canary = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    canary.bind(('::', 11941))
    canary.settimeout(.5)
    ns_user("import socket; s=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)\ntry: s.sendto(b'no-ipv6-leak',('fd44::1',11941))\nexcept OSError: pass")
    try:
        canary.recvfrom(1024)
        raise AssertionError('IPv6 escaped')
    except socket.timeout:
        results['ipv6_blocked'] = True
    canary.close()
finally:
    supervisor.cleanup()

content = hashlib.sha256(b'VPN integration fixture').digest() * (4*1024*1024//32)
seen_sources = []
dns_sources = []
dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
dns.bind(('198.18.0.1', 53))
dns.settimeout(.5)
dns_done = threading.Event()
def dns_server():
    while not dns_done.is_set():
        try:
            data, address = dns.recvfrom(4096)
            dns_sources.append(address[0])
            if len(data) >= 12:
                reply = data[:2] + struct.pack('!HHHHH', 0x8183, 1, 0, 0, 0) + data[12:]
                dns.sendto(reply, address)
        except socket.timeout:
            pass
threading.Thread(target=dns_server, daemon=True).start()
class HTTP(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_GET(self):
        seen_sources.append(self.client_address[0])
        if self.path.startswith('/announce'):
            body = b'd8:intervali1800e5:peers0:e'
            self.send_response(200)
        else:
            start, end = 0, len(content)-1
            if self.headers.get('Range'):
                start, finish = self.headers['Range'].removeprefix('bytes=').split('-')
                start, end = int(start), int(finish) if finish else end
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(content)}')
            else:
                self.send_response(200)
            body = content[start:end+1]
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

http = http.server.ThreadingHTTPServer(('198.18.0.1', 20002), HTTP)
threading.Thread(target=http.serve_forever, daemon=True).start()
server_log = (out/'server.log').open('w')
server = subprocess.Popen(['openvpn', '--config', str(out/'server.conf')], stdout=server_log, stderr=server_log)
client_log = (out/'supervisor.log').open('w')
client = None

def state():
    try:
        return json.loads((controller.RUNTIME/'status.json').read_text())['state']
    except (FileNotFoundError, json.JSONDecodeError):
        return None

token = ''
def rpc(method, params=None):
    global token
    for _ in range(2):
        req = urllib.request.Request('http://127.0.0.1:19091/transmission/rpc',
            data=json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params or {})).encode(),
            headers={'X-Transmission-Session-Id': token})
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                body = json.load(response)
                assert 'error' not in body, body
                return body['result']
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise
            token = error.headers['X-Transmission-Session-Id']
    raise AssertionError('RPC handshake failed')

def bencode(value):
    if isinstance(value, int): return b'i'+str(value).encode()+b'e'
    if isinstance(value, str): value = value.encode()
    if isinstance(value, bytes): return str(len(value)).encode()+b':'+value
    if isinstance(value, list): return b'l'+b''.join(map(bencode, value))+b'e'
    return b'd'+b''.join(bencode(k)+bencode(v) for k,v in sorted(value.items()))+b'e'

try:
    wait_for(lambda: 'Initialization Sequence Completed' in (out/'server.log').read_text())
    client = subprocess.Popen(['python3', str(source/'controller.py'), 'run', '--config', str(out/'vpn.json')],
                              stdout=client_log, stderr=client_log)
    wait_for(lambda: state() == 'connected' or client.poll() is not None)
    assert client.poll() is None, (out/'supervisor.log').read_text()
    assert state() == 'connected'
    def rpc_ready():
        try:
            return bool(rpc('session_get'))
        except (OSError, urllib.error.URLError):
            return False
    wait_for(rpc_ready, timeout=20)
    results['real_tls_vpn_connected'] = True
    vpn_log = json.loads((out/'config/vpn-log.json').read_text())
    assert any(item['message'] == 'VPN tunnel connected' for item in vpn_log['entries'])
    assert all('fixture-password' not in item['message'] for item in vpn_log['entries'])
    results['vpn_log_public_bounded_and_secret_free'] = True
    # A real GUI-helper test while the production-style VPN remains connected.
    settings_before = (out/'config/settings.json').read_bytes()
    status_before = (out/'config/vpn-status.json').read_bytes()
    service = subprocess.Popen(['python3', str(source/'probe_service.py'), '--user', 'nobody'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        socket_path = Path('/run/transmission-vpn-test-control/probe.sock')
        wait_for(socket_path.exists, 5)
        request = dict(profile=profile.crypto + f'\nremote 192.0.2.2 11940\nproto {transport}\n',
                       secret='probe-user\nfixture-password\n', timeout=10)
        client_code = '''import socket,sys
with socket.socket(socket.AF_UNIX) as s:
 s.settimeout(30); s.connect('/run/transmission-vpn-test-control/probe.sock')
 s.sendall(sys.stdin.buffer.read()); s.shutdown(socket.SHUT_WR)
 data=b''
 while True:
  part=s.recv(4096)
  if not part: break
  data+=part
 print(data.decode())
'''
        def run_probe(payload):
            result = subprocess.run(['setpriv', '--reuid=65534', '--regid=65534', '--clear-groups',
                                     'python3', '-c', client_code], input=json.dumps(payload),
                                     capture_output=True, text=True, timeout=35)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        checked = run_probe(request)
        assert checked['state'] == 'tested', checked
        results['probe_authentication_and_tunnel'] = True
        results['probe_elapsed_ms'] = checked['elapsed_ms']
        assert run_probe(dict(request, secret='probe-rejected\nwrong\n'))['state'] == 'error'
        results['probe_rejected_authentication'] = True
        assert run_probe(dict(request, profile=request['profile'].replace('11940', '11949'), timeout=5))['state'] == 'error'
        results['probe_timeout'] = True
        assert (out/'config/settings.json').read_bytes() == settings_before
        assert (out/'config/vpn-status.json').read_bytes() == status_before
        assert state() == 'connected' and rpc_ready()
        assert 'transmission-vpn-test' not in cmd('ip', 'netns', 'list')
        assert not Path('/etc/netns/transmission-vpn-test').exists()
        assert not Path('/run/transmission-vpn-test/auth.txt').exists()
        assert subprocess.run(['nft', 'list', 'table', 'ip', 'transmission_vpn_test'], capture_output=True).returncode != 0
        results['probe_cleanup_and_production_unchanged'] = True
    finally:
        service.terminate()
        service.wait(timeout=5)
    dashboard = rpc('session_stats', {'include_history': True})
    assert dashboard['vpn_status']['state'] == 'connected', dashboard
    assert dashboard['vpn_status']['provider'] == 'purevpn'
    assert dashboard['vpn_status']['protocol'] == transport
    assert set(dashboard['vpn_status']) <= {'state', 'provider', 'protocol', 'kill_switch', 'changed_at'}
    assert 'transfer_history' not in rpc('session_stats')
    results['dashboard_vpn_metadata_safe_and_opt_in'] = True
    time.sleep(.3)
    rpc('session_set', dict(dht_enabled=False, pex_enabled=False, utp_enabled=False))
    pieces = b''.join(hashlib.sha1(content[n:n+262144]).digest() for n in range(0,len(content),262144))
    metainfo = bencode({b'announce': b'http://198.18.0.1:20002/announce',
        b'url-list': b'http://198.18.0.1:20002/',
        b'info': {b'name': b'payload.bin', b'length': len(content), b'piece length': 262144, b'pieces': pieces}})
    began = time.monotonic()
    torrent = rpc('torrent_add', dict(metainfo=base64.b64encode(metainfo).decode(), paused=False))['torrent_added']['id']
    def complete():
        row = rpc('torrent_get', dict(ids=[torrent], fields=['status','error','error_string','percent_done']))['torrents'][0]
        assert not row['error'], row
        return row['status'] == 6
    wait_for(complete, 45)
    results['download_seconds'] = time.monotonic()-began
    assert (out/'downloads/payload.bin').read_bytes() == content
    assert seen_sources and all(ip.startswith('10.44.') for ip in seen_sources), seen_sources
    results['webseed_bytes_verified_over_vpn'] = len(content)
    dashboard = rpc('session_stats', {'include_history': True})
    history_down = sum(row[1] for row in dashboard['transfer_history']['days'])
    assert history_down == dashboard['current_stats']['downloaded_bytes'] == len(content), dashboard
    results['dashboard_history_counts_real_transfer'] = True
    results['tracker_and_webseed_sources'] = sorted(set(seen_sources))
    # A public RPC socket must not exist, nor may RPC be reachable from the tunnel.
    listeners = cmd('ss', '-ltn')
    assert '127.0.0.1:19091' in listeners and '0.0.0.0:19091' not in listeners
    results['rpc_loopback_only'] = True
    saved = {k.replace('-', '_'):v for k,v in json.loads((out/'config/settings.json').read_text()).items()}
    assert not saved['port_forwarding_enabled']
    results['upnp_disabled'] = True
    pids = cmd('ip', 'netns', 'pids', controller.NS).split()
    daemon_pid = next(pid for pid in pids if b'transmission-daemon' in Path(f'/proc/{pid}/cmdline').read_bytes())
    daemon_status = Path(f'/proc/{daemon_pid}/status').read_text()
    assert 'Uid:\t65534\t65534\t65534\t65534' in daemon_status
    assert 'CapEff:\t0000000000000000' in daemon_status
    results['daemon_unprivileged'] = True
    def resources(pid):
        values = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        ticks = int(values[11]) + int(values[12])
        status = Path(f'/proc/{pid}/status').read_text().splitlines()
        rss = int(next(line for line in status if line.startswith('VmRSS:')).split()[1])
        cpu_ns = int(Path(f'/proc/{pid}/schedstat').read_text().split()[0])
        switches = int(next(line for line in status if line.startswith('voluntary_ctxt_switches:')).split()[1])
        pss = int(next(line for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines() if line.startswith('Pss:')).split()[1])
        return ticks, rss, cpu_ns, switches, pss
    children = Path(f'/proc/{client.pid}/task/{client.pid}/children').read_text().split()
    roles = {'supervisor':str(client.pid)}
    for pid in children:
        name = Path(f'/proc/{pid}/cmdline').read_bytes()
        for role, needle in [('openvpn',b'openvpn'),('transmission',b'transmission-daemon'),('rpc_proxy',b'socat'),('rpc_proxy',b'rpc-relay')]:
            if needle in name:
                roles[role] = pid
    sample_seconds = int(os.environ.get('VPN_BENCH_SECONDS','4'))
    if os.environ.get('VPN_BENCH_FAST_IO_ABBA') == '1':
        assert transport == 'udp'
        from benchmark_transfer import compare_fast_io
        rpc('torrent_stop', dict(ids=[torrent]))
        results['fast_io_abba'] = compare_fast_io(controller.NS, roles['openvpn'],
                                                 controller.RUNTIME/'client.ovpn', controller.RUNTIME/'openvpn.log')
    if os.environ.get('VPN_BENCH_TRANSFER') == '1':
        from benchmark_transfer import measure
        rpc('torrent_stop', dict(ids=[torrent]))
        results['transfer_benchmark'] = measure(controller.NS, roles['openvpn'])
        results['data_channel'] = [line for line in (controller.RUNTIME/'openvpn.log').read_text().splitlines()
                                   if 'Data Channel:' in line or 'DCO' in line]
    if sample_seconds > 4:
        rpc('torrent_stop',dict(ids=[torrent]))
        time.sleep(2)
    def packets():
        result = json.loads(cmd('ip','netns','exec',controller.NS,'nft','-j','list','counter','inet',controller.TABLE,'benchmark'))
        return next(item['counter']['packets'] for item in result['nftables'] if 'counter' in item)
    if os.environ.get('VPN_BENCH_KEEPALIVE') == '1':
        cmd('ip','netns','exec',controller.NS,'nft','add','counter','inet',controller.TABLE,'benchmark')
        cmd('ip','netns','exec',controller.NS,'nft','insert','rule','inet',controller.TABLE,'output',
            'oifname','uplink','meta','skuid','0','ip','daddr','192.0.2.2',transport,'dport','11940','counter','name','benchmark')
        packets_before = packets()
        results['negotiated_timers'] = [line.split('Timers:',1)[1].strip()
            for line in (controller.RUNTIME/'openvpn.log').read_text().splitlines() if 'Timers:' in line]
        captures = []
        capture_done = threading.Event()
        capture = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x0800))
        capture.bind((controller.HOST_LINK,0))
        capture.settimeout(.2)
        capture_started = time.monotonic()
        def capture_packets():
            while not capture_done.is_set():
                try:
                    packet = capture.recv(65536)
                    if len(packet)<42 or packet[23]!=17 or packet[26:30]!=socket.inet_aton(c['guest_ip']):
                        continue
                    offset = 14+(packet[14]&15)*4
                    if int.from_bytes(packet[offset+2:offset+4],'big')!=11940:
                        continue
                    captures.append({'seconds':time.monotonic()-capture_started,
                                     'udp_bytes':int.from_bytes(packet[offset+4:offset+6],'big'),
                                     'opcode':packet[offset+8]>>3})
                except socket.timeout:
                    pass
        capture_thread = threading.Thread(target=capture_packets,daemon=True)
        capture_thread.start()
    before_usage = {role: resources(pid) for role,pid in roles.items()}
    idle_start = time.monotonic()
    time.sleep(sample_seconds)
    elapsed = time.monotonic()-idle_start
    after_usage = {role: resources(pid) for role,pid in roles.items()}
    results['idle_seconds'] = elapsed
    results['idle_resources'] = {role: {'rss_kib':values[1], 'pss_kib':values[4],
        'cpu_ms_main_thread':(values[2]-before_usage[role][2])/1e6,
        'voluntary_context_switches':values[3]-before_usage[role][3],
        'cpu_percent_one_core':(values[0]-before_usage[role][0])/os.sysconf('SC_CLK_TCK')/elapsed*100}
        for role,values in after_usage.items()}
    if os.environ.get('VPN_BENCH_KEEPALIVE') == '1':
        results['idle_client_vpn_packets'] = packets()-packets_before
        capture_done.set()
        capture_thread.join(timeout=1)
        capture.close()
        results['idle_vpn_packet_samples'] = captures
    ns_user("import socket\ntry: socket.getaddrinfo('vpn-fixture.invalid',80)\nexcept socket.gaierror: pass")
    assert dns_sources and all(ip.startswith('10.44.') for ip in dns_sources), dns_sources
    results['dns_only_through_tunnel'] = True
    try:
        connection = socket.create_connection(('10.44.0.2',19091), timeout=.5)
        connection.close()
        raise AssertionError('RPC exposed on the VPN tunnel')
    except (TimeoutError, OSError):
        results['rpc_blocked_from_tunnel'] = True
    # Keep an established UDP socket across route loss: generic conntrack accept
    # rules must not allow a previously tunneled flow to fall back to the underlay.
    established = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    established.bind(('198.18.0.1',11942))
    established.settimeout(2)
    probe_code = """import socket,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(2)
s.connect(('198.18.0.1',11942)); s.send(b'before'); s.recv(32)
print('ready',flush=True); sys.stdin.readline()
try: s.send(b'after')
except OSError: pass
"""
    flow = subprocess.Popen(['ip','netns','exec',controller.NS,'setpriv','--reuid=65534','--regid=65534',
                             '--clear-groups','python3','-c',probe_code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    data, address = established.recvfrom(32)
    assert data == b'before' and address[0].startswith('10.44.')
    established.sendto(b'ack',address)
    assert flow.stdout.readline().strip() == 'ready'
    # Force fallback routing while existing RPC and torrent sockets still exist.
    cmd('ip', '-n', controller.NS, 'route', 'del', '0.0.0.0/1')
    cmd('ip', '-n', controller.NS, 'route', 'del', '128.0.0.0/1')
    flow.communicate('continue\n',timeout=3)
    assert flow.returncode == 0
    established.settimeout(.5)
    try:
        established.recvfrom(32)
        raise AssertionError('Established flow escaped through the underlay')
    except socket.timeout:
        results['established_flow_fallback_blocked'] = True
    established.close()
    before = len(seen_sources)
    ns_user("import socket; s=socket.socket(); s.settimeout(.5)\ntry: s.connect(('198.18.0.1',20002)); raise AssertionError('leak')\nexcept (TimeoutError, OSError): pass")
    assert len(seen_sources) == before
    results['fallback_route_blocked'] = True
    assert rpc('session_get')
    results['rpc_survives_tunnel_route_loss'] = True
    # A real OpenVPN reconnect restores the tunnel routes; the daemon is retained.
    vpn_pid = next(pid for pid in pids if b'openvpn' in Path(f'/proc/{pid}/cmdline').read_bytes())
    os.kill(int(vpn_pid), signal.SIGHUP)
    time.sleep(.5)
    wait_for(lambda: '0.0.0.0/1' in cmd('ip','-n',controller.NS,'route','show'), 20)
    assert Path(f'/proc/{daemon_pid}').exists()
    results['reconnect_preserves_daemon'] = True
    # VPN process death shuts down Transmission; teardown removes only owned state.
    os.kill(int(vpn_pid), signal.SIGKILL)
    client.wait(timeout=35)
    assert client.returncode == 1
    assert not Path(f'/proc/{daemon_pid}').exists()
    results['vpn_death_stops_daemon'] = True
    assert controller.NS not in cmd('ip', 'netns', 'list')
    assert subprocess.run(['nft','list','table','ip',controller.TABLE], capture_output=True).returncode != 0
    assert not controller.RESOLVER.exists()
    assert not (controller.RUNTIME/'auth.txt').exists()
    results['cleanup_verified'] = True
    # Restart on the same RPC port, including TIME_WAIT from previous sessions.
    client = subprocess.Popen(['python3',str(source/'controller.py'),'run','--config',str(out/'vpn.json')],
                              stdout=client_log,stderr=client_log)
    wait_for(lambda: state() == 'connected', 20)
    wait_for(rpc_ready, 10)
    dashboard = rpc('session_stats', {'include_history': True})
    assert sum(row[1] for row in dashboard['transfer_history']['days']) >= history_down
    assert dashboard['vpn_status']['state'] == 'connected'
    results['dashboard_history_survives_daemon_restart'] = True
    children = Path(f'/proc/{client.pid}/task/{client.pid}/children').read_text().split()
    proxy_pid = next(pid for pid in children if b'rpc-relay' in Path(f'/proc/{pid}/cmdline').read_bytes())
    restarted_daemon = next(pid for pid in children if b'transmission-daemon' in Path(f'/proc/{pid}/cmdline').read_bytes())
    results['immediate_restart_same_rpc_port'] = True
    killed_at = time.monotonic()
    os.kill(int(proxy_pid), signal.SIGKILL)
    client.wait(timeout=10)
    assert client.returncode == 1
    assert not Path(f'/proc/{restarted_daemon}').exists()
    assert controller.NS not in cmd('ip','netns','list')
    results['proxy_death_stops_daemon_promptly'] = True
    results['proxy_death_shutdown_seconds'] = time.monotonic() - killed_at
    # Failure to establish a tunnel never starts Transmission.
    old_log_time = (controller.RUNTIME/'transmission.log').stat().st_mtime_ns
    (out/'client.ovpn').write_text((out/'client.ovpn').read_text().replace('11940','11949'))
    config['start_timeout'] = 5
    config['rpc_port'] = 19092
    (out/'vpn.json').write_text(json.dumps(config))
    log_offset = (out/'supervisor.log').stat().st_size
    client = subprocess.Popen(['python3',str(source/'controller.py'),'run','--config',str(out/'vpn.json')],
                              stdout=client_log,stderr=client_log)
    client.wait(timeout=12)
    assert client.returncode == 1
    assert (controller.RUNTIME/'transmission.log').stat().st_mtime_ns == old_log_time
    assert state() == 'failed'
    assert 'VPN connection timed out' in (out/'supervisor.log').read_text()[log_offset:]
    assert controller.NS not in cmd('ip','netns','list')
    results['startup_timeout_never_starts_daemon'] = True
finally:
    if client is not None and client.poll() is None:
        client.terminate()
        client.wait(timeout=80)
    server.terminate()
    server.wait(timeout=10)
    server_log.close()
    client_log.close()
    for name in ('openvpn.log', 'transmission.log', 'rpc.log', 'status.json'):
        path = controller.RUNTIME/name
        if path.exists():
            (out/name).write_bytes(path.read_bytes())
    http.shutdown()
    dns_done.set()
    (out/'results.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
