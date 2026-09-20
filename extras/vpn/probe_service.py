#!/usr/bin/env python3
"""On-demand, authenticated local VPN probes. Install code root-owned before use."""
import argparse
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time

import controller as vpn

SOCKET = '/run/transmission-vpn-test-control/probe.sock'
LIMIT = 65536


def receive(stream):
    data = bytearray()
    while True:
        chunk = stream.recv(min(8192, LIMIT + 1 - len(data)))
        if not chunk:
            return json.loads(data)
        data.extend(chunk)
        if len(data) > LIMIT:
            raise ValueError('Request too large')


def decode_request(request, folder):
    if not isinstance(request, dict) or set(request) != {'profile', 'secret', 'timeout'}:
        raise ValueError('Invalid request')
    if type(request['timeout']) is not int or not 5 <= request['timeout'] <= 60:
        raise ValueError('Invalid timeout')
    if not isinstance(request['profile'], str) or len(request['profile'].encode()) > 60000:
        raise ValueError('Invalid profile')
    if not isinstance(request['secret'], str) or len(request['secret'].encode()) > 4096:
        raise ValueError('Invalid credentials')
    profile_path = folder / 'profile.ovpn'
    secret_path = folder / 'credentials'
    vpn.secure_write(profile_path, request['profile'])
    vpn.secure_write(secret_path, request['secret'])
    vpn.credentials(secret_path)
    profile = vpn.PROVIDERS['purevpn'].load(profile_path, allow_external=False)
    return profile, secret_path


def probe(request, user):
    # Constants are process-local: production resources are never selected.
    vpn.NS = 'transmission-vpn-test'
    vpn.HOST_LINK = 'trvpntest0'
    vpn.TABLE = 'transmission_vpn_test'
    vpn.RUNTIME = Path('/run/transmission-vpn-test')
    vpn.RESOLVER = Path('/etc/netns') / vpn.NS
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='transmission-probe-', dir='/run') as directory:
        folder = Path(directory)
        profile, secret = decode_request(request, folder)
        # Resolve once; reusing the literal avoids DNS changes between validation and connection.
        endpoint = profile.resolve()
        profile = profile._replace(hostname=endpoint)
        routes = json.loads(vpn.command('ip', '-j', '-4', 'route', 'show', 'table', 'all').stdout)
        occupied = [ipaddress.IPv4Network(row['dst'], strict=False)
                    for row in routes if row.get('dst', 'default') != 'default']
        occupied.append(ipaddress.IPv4Network(endpoint + '/32'))
        network = next((ipaddress.IPv4Network(f'10.203.{octet}.0/30') for octet in range(75, 255)
                        if not any(ipaddress.IPv4Network(f'10.203.{octet}.0/30').overlaps(n) for n in occupied)), None)
        if network is None:
            raise ValueError('No unused test subnet')
        host, guest = map(str, network.hosts())
        config = dict(provider='purevpn', credentials_file=str(secret), daemon='/unused',
                      config_dir=str(folder), download_dir=str(folder), dns=[], rpc_port=65534,
                      subnet=str(network), host_ip=host, guest_ip=guest, start_timeout=request['timeout'])
        supervisor = vpn.Supervisor(config, user, profile, probe=True)
        def stop(*_):
            raise RuntimeError('Probe interrupted')
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGALRM, stop)
        signal.alarm(90)
        try:
            # The controller emits state transitions; only the structured result goes to stdout.
            with contextlib.redirect_stdout(sys.stderr):
                connected = supervisor.run()
            if not connected:
                raise RuntimeError('Probe interrupted')
            return dict(state='tested', checks={'authentication': 'ok', 'tunnel': 'ok'},
                        elapsed_ms=round((time.monotonic() - started) * 1000))
        finally:
            signal.alarm(0)


def serve(user):
    directory = Path(SOCKET).parent
    directory.mkdir(mode=0o755, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Control directory must be root-owned and not writable by others')
    lock = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    Path(SOCKET).unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(SOCKET)
        try:
            os.chmod(SOCKET, 0o600)
            os.chown(SOCKET, user.pw_uid, user.pw_gid)
            server.listen(1)
            while True:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(5)
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != user.pw_uid:
                        continue
                    try:
                        request = receive(connection)
                        result = subprocess.run([sys.executable, __file__, '--worker', '--user', user.pw_name],
                            input=json.dumps(request), text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=120, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'})
                        response = json.loads(result.stdout) if result.returncode == 0 else {'state': 'error'}
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        response = {'state': 'error'}
                    try:
                        connection.sendall(json.dumps(response).encode())
                    except OSError:
                        pass
        finally:
            Path(SOCKET).unlink(missing_ok=True)
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='Only this daemon user may request tests')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    user = pwd.getpwnam(args.user)
    if os.geteuid() != 0 or user.pw_uid == 0:
        parser.error('Run as root, with a non-root daemon user')
    if args.worker:
        try:
            raw = sys.stdin.buffer.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError('Request too large')
            response = probe(json.loads(raw), user)
        except Exception:
            response = {'state': 'error'}
        print(json.dumps(response))
    else:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        serve(user)


if __name__ == '__main__':
    main()
