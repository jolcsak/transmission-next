"""Real sockets: backpressure, half-close, capacity, reset and shutdown."""
from pathlib import Path
import os
import socket
import socketserver
import struct
import subprocess
import threading
import time
import unittest

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            mode = self.request.recv(1)
            if mode == b'X':
                self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii',1,0))
                return
            while True:
                data = self.request.recv(8192)
                if not data:
                    break
                self.request.sendall(data)
            self.request.shutdown(socket.SHUT_WR)
        except OSError:
            pass

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

class RelayTest(unittest.TestCase):
    def setUp(self):
        self.server = Server(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0))
            self.port = probe.getsockname()[1]
        self.proc = subprocess.Popen([os.environ.get('RPC_RELAY_BIN',str(Path(__file__).with_name('rpc-relay'))),str(self.port),
            '127.0.0.1',str(self.server.server_address[1])],stderr=subprocess.PIPE)
        end = time.monotonic()+3
        while True:
            try:
                with self.connect():
                    break
            except ConnectionRefusedError:
                if time.monotonic()>end:
                    self.fail('relay did not start')
                time.sleep(.01)
        time.sleep(.05)

    def connect(self):
        return socket.create_connection(('127.0.0.1',self.port),timeout=5)

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(timeout=2)
        self.proc.stderr.close()
        self.server.shutdown()
        self.server.server_close()

    def test_large_full_duplex_and_half_close(self):
        payload = bytes(range(256))*32768
        errors = []
        with self.connect() as client:
            def send():
                try:
                    client.sendall(b'E'+payload)
                    client.shutdown(socket.SHUT_WR)
                except Exception as error:
                    errors.append(error)
            sender = threading.Thread(target=send)
            sender.start()
            # Force buffered data/backpressure before draining a multi-MiB reply.
            time.sleep(.1)
            result = bytearray()
            while True:
                data = client.recv(7111)
                if not data:
                    break
                result.extend(data)
            sender.join(timeout=3)
            self.assertFalse(sender.is_alive())
            self.assertFalse(errors)
            self.assertEqual(result,payload)
        self.assertIsNone(self.proc.poll())

    def test_capacity_and_reuse_without_fork(self):
        clients = [self.connect() for _ in range(16)]
        try:
            for client in clients:
                client.sendall(b'Eok')
                self.assertEqual(client.recv(2),b'ok')
            children = Path(f'/proc/{self.proc.pid}/task/{self.proc.pid}/children').read_text()
            self.assertEqual(children.strip(),'')
            with self.connect() as rejected:
                try:
                    self.assertEqual(rejected.recv(1),b'')
                except ConnectionResetError:
                    pass
        finally:
            for client in clients:
                client.close()
        time.sleep(.1)
        with self.connect() as client:
            client.sendall(b'Eagain')
            self.assertEqual(client.recv(5),b'again')

    def test_reset_does_not_kill_relay(self):
        with self.connect() as client:
            client.sendall(b'X')
            try:
                self.assertEqual(client.recv(1),b'')
            except ConnectionResetError:
                pass
        with self.connect() as client:
            client.sendall(b'Ealive')
            self.assertEqual(client.recv(5),b'alive')

    def test_idle_shutdown_is_immediate(self):
        start = time.monotonic()
        self.proc.terminate()
        self.assertEqual(self.proc.wait(timeout=1),0)
        self.assertLess(time.monotonic()-start,1)

if __name__ == '__main__':
    unittest.main()
