import http.server
import json
import socket
import threading
import unittest

from exporter import RPC, LIMIT, comparable

SAMPLE = dict(schema_version=1, sampled_at=1, storage_status={}, vpn_status={},
              current_stats={}, cumulative_stats={})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def get_request(self):
        sock, addr = super().get_request()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connections += 1
        return sock, addr


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.headers.get('X-Transmission-Session-Id') != 'test-token':
            self.send_response(409)
            self.send_header('X-Transmission-Session-Id', 'test-token')
            body = b''
        else:
            self.send_response(self.server.status)
            body = self.server.body
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Transport(unittest.TestCase):
    def setUp(self):
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.connections = 0
        self.server.status = 200
        self.server.body = json.dumps({'result': SAMPLE}).encode()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.rpc = RPC({'url': f'http://127.0.0.1:{self.server.server_port}/transmission/rpc'})

    def tearDown(self):
        self.rpc.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_reuse_and_reopen(self):
        for _ in range(20):
            self.assertEqual(self.rpc.sample(False), SAMPLE)
        self.assertEqual(self.server.connections, 1)
        self.rpc.close()
        self.assertEqual(self.rpc.sample(False), SAMPLE)
        self.assertEqual(self.server.connections, 2)

    def test_reject_then_recover(self):
        for status, body in ((401, b''), (302, b''), (200, b'not-json'),
                             (200, b'{"result":[]}'), (200, b'x' * (LIMIT + 1))):
            self.server.status, self.server.body = status, body
            with self.assertRaises(ValueError):
                self.rpc.sample(False)
        self.server.status = 200
        self.server.body = json.dumps({'result': SAMPLE}).encode()
        self.assertEqual(self.rpc.sample(False), SAMPLE)

    def test_comparison_preserves_meaningful_changes(self):
        a = dict(SAMPLE, current_stats={'seconds_active': 1, 'downloaded_bytes': 5},
                 transfer_history={'now': 1, 'days': [[0, 5, 0]]})
        b = dict(a, sampled_at=2, current_stats={'seconds_active': 2, 'downloaded_bytes': 5},
                 transfer_history={'now': 2, 'days': [[0, 5, 0]]})
        self.assertEqual(comparable(a, True), comparable(b, True))
        b['current_stats']['downloaded_bytes'] = 6
        self.assertNotEqual(comparable(a, True), comparable(b, True))
        b = dict(a, storage_status={'system_state': 'critical'})
        self.assertNotEqual(comparable(a, True), comparable(b, True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
