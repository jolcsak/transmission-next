"""Local, isolated TLS integration fixture: measure OpenVPN CPU per verified MiB."""
import hashlib
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import threading
import time

BLOCK = hashlib.shake_256(b'vpn-cpu-fixture').digest(65536)


def send(stream, size):
    for _ in range(size // len(BLOCK)):
        stream.sendall(BLOCK)
    stream.shutdown(socket.SHUT_WR)


def receive(stream, size):
    digest = hashlib.sha256()
    count = 0
    while data := stream.recv(65536):
        digest.update(data)
        count += len(data)
    expected = hashlib.sha256()
    for _ in range(size // len(BLOCK)):
        expected.update(BLOCK)
    assert count == size and digest.digest() == expected.digest(), (count, size)


def measure(namespace, vpn_pid, mib=1024):
    results = []
    for direction in ('download', 'upload'):
        errors = []
        size = mib * 1024 * 1024
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('198.18.0.1', 20003))
            listener.listen(1)
            listener.settimeout(90)
            def serve():
                try:
                    with listener.accept()[0] as stream:
                        stream.settimeout(90)
                        (send if direction == 'download' else receive)(stream, size)
                except BaseException as error:
                    errors.append(repr(error))
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            def cpu_ns():
                return int(Path(f'/proc/{vpn_pid}/schedstat').read_text().split()[0])
            before = cpu_ns()
            began = time.monotonic()
            subprocess.run(['ip', 'netns', 'exec', namespace, 'setpriv', '--reuid=65534',
                            '--regid=65534', '--clear-groups', '--no-new-privs', '--bounding-set=-all',
                            'python3', str(Path(__file__).resolve()), direction, str(size)],
                           check=True, timeout=100)
            worker.join(timeout=5)
            assert not worker.is_alive() and not errors, errors
            elapsed = time.monotonic() - began
            cpu_ms = (cpu_ns() - before) / 1e6
            results.append(dict(direction=direction, verified_mib=mib, seconds=elapsed,
                                client_openvpn_cpu_ms=cpu_ms, cpu_ms_per_mib=cpu_ms/mib))
    return results


def compare_fast_io(namespace, vpn_pid, config, log):
    """Change only fast-io inside the isolated fixture; reuse the TLS server/PID."""
    assert os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net')
    original = config.read_text()
    base = '\n'.join(line for line in original.splitlines() if line.strip() != 'fast-io') + '\n'
    def reload(text):
        offset = log.stat().st_size
        config.write_text(text)
        os.kill(int(vpn_pid), signal.SIGHUP)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if 'Initialization Sequence Completed' in log.read_text()[offset:]:
                time.sleep(.3)
                return
            time.sleep(.05)
        raise AssertionError('VPN reload failed')
    results = []
    try:
        for enabled in (False, True, True, False, False, True, True, False):
            reload(base + ('fast-io\n' if enabled else ''))
            results.append(dict(fast_io=enabled, transfers=measure(namespace, vpn_pid)))
    finally:
        reload(original)
    return results


if __name__ == '__main__':
    direction, size = sys.argv[1], int(sys.argv[2])
    with socket.create_connection(('198.18.0.1', 20003), timeout=90) as stream:
        (receive if direction == 'download' else send)(stream, size)
