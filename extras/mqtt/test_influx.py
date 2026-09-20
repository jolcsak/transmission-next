import copy
import http.server
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from influx import Influx, encode
from exporter import Exporter

SAMPLE = dict(schema_version=1, sampled_at=1700000000, download_speed=42, upload_speed=10,
    torrent_count=2, storage_status=dict(system_state='normal', cpu_wait_percent=0,
        directories=[dict(path='/data/a,b "x"\nline', profile='hdd', load='normal', torrents=2, active=1)]),
    vpn_status=dict(state='connected'), current_stats={}, cumulative_stats=dict(downloaded_bytes=100))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.server.received.append((self.path, self.headers.get('Authorization'), body))
        self.send_response(self.server.status)
        self.send_header('Content-Length', '0')
        self.end_headers()


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        token = Path(self.temp.name) / 'token'
        token.write_text('test-token')
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.received, self.server.status = [], 204
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.config = dict(enabled=True, url=f'http://127.0.0.1:{self.server.server_port}',
            org='org name', bucket='metrics', token_file=str(token), buffer_bytes=4096)
        self.sink = Influx(self.config)

    def tearDown(self):
        self.sink.close()
        self.sink.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_line_protocol(self):
        lines = list(encode(SAMPLE, 'host', True))
        self.assertEqual(len(lines), 2)
        self.assertIn(b'cpu_wait_percent=0.0', lines[0])
        self.assertIn(b'download_speed=42i', lines[0])
        self.assertEqual(lines[1].count(b'\n'), 1)
        self.assertIn(b'profile_id=hdd', lines[1])
        self.assertTrue(lines[0].endswith(b' 1700000000000000000\n'))

    def test_vpn_traffic_export(self):
        from exporter import comparable
        sample = copy.deepcopy(SAMPLE)
        sample['vpn_status'].update(received_bytes=12345678901, sent_bytes=42,
            received_packets=12, sent_packets=3, traffic_scope='current_tunnel', traffic_sampled_at=1700000000)
        line = next(encode(sample, 'host'))
        self.assertIn(b'vpn_received_bytes=12345678901i', line)
        self.assertIn(b'vpn_sent_bytes=42i', line)
        self.assertIn(b'vpn_traffic_scope="current_tunnel"', line)
        later = copy.deepcopy(sample)
        later['vpn_status']['traffic_sampled_at'] += 10
        self.assertEqual(comparable(sample, False), comparable(later, False))
        later['vpn_status']['received_bytes'] += 1
        self.assertNotEqual(comparable(sample, False), comparable(later, False))

    def test_batch_and_persistent_connection(self):
        self.sink.add(SAMPLE)
        self.sink.add(dict(SAMPLE, sampled_at=1700000030))
        self.assertEqual(len(self.server.received), 0)
        body = b''.join(self.sink.queue)
        self.assertTrue(self.sink.write(body))
        sock = self.sink.connection.sock
        self.assertTrue(self.sink.write(body))
        self.assertIs(self.sink.connection.sock, sock)
        path, auth, payload = self.server.received[0]
        self.assertIn('org=org+name', path)
        self.assertIn('precision=ns', path)
        self.assertEqual(auth, 'Token test-token')
        self.assertEqual(payload.count(b'\n'), 2)

    def test_bounded_outage_buffer(self):
        for i in range(10000):
            self.sink.add(dict(SAMPLE, sampled_at=1700000000+i))
        self.assertLessEqual(self.sink.size, 4096)
        self.assertGreater(self.sink.dropped, 0)
        self.assertIn(b'1700009999', self.sink.queue[-1])
        self.server.status = 503
        self.assertFalse(self.sink.write(b''.join(self.sink.queue)))
        self.server.status = 204
        self.assertTrue(self.sink.write(b''.join(self.sink.queue)))

    def test_worker_retry_and_shutdown_flush(self):
        self.sink.interval = .02
        self.server.status = 503
        self.sink.add(SAMPLE)
        self.sink.start()
        deadline = time.monotonic() + 3
        while not self.server.received and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.received)
        self.server.status = 204
        self.sink.close()
        self.assertFalse(self.sink.thread.is_alive())
        self.assertEqual(self.sink.size, 0)
        self.assertGreaterEqual(len(self.server.received), 2)

    def test_influx_only_shared_sampling(self):
        config = dict(influxdb=self.config, mqtt={'enabled': False})
        exporter = Exporter(config)
        exporter.interval = .01
        samples = []
        def sample(history):
            self.assertFalse(history)
            samples.append(1)
            if len(samples) == 3:
                exporter.stop.set()
            return SAMPLE
        exporter.rpc.sample = sample
        exporter.rpc.security = lambda: {}
        exporter.run()
        self.assertEqual(len(samples), 3)
        self.assertIsNone(exporter.client)
        self.assertEqual(self.server.received[-1][2].count(b'\n'), 3)

    def test_optional_and_validation(self):
        with self.assertRaises(ValueError):
            Exporter({'influxdb': {'enabled': False}})
        for update in ({'buffer_bytes': 1}, {'flush_seconds': 0}, {'url': 'http://user:pass@host'}):
            with self.assertRaises(ValueError):
                Influx(dict(self.config, **update))

    def test_security_records_share_bounded_writer(self):
        from security_export import SecurityExport
        from test_security_export import row
        events = SecurityExport().collect(dict(entries=[row(), row()]))
        self.sink.add_security(events)
        body = b''.join(self.sink.queue)
        self.assertEqual(body.count(b'\n'), 2)
        self.assertIn(b'transmission_security,instance=transmission,event_code=rpc_auth_failed ', body)
        self.assertIn(b'event_id="', body)
        self.assertNotIn(b',event_id=', body)
        self.assertNotEqual(body.splitlines()[0].rsplit(b' ',1)[1], body.splitlines()[1].rsplit(b' ',1)[1])
        self.assertTrue(self.sink.write(body))
        self.assertEqual(self.server.received[-1][2], body)
        for _ in range(100): self.sink.add_security(events)
        self.assertLessEqual(self.sink.size, self.sink.limit)
        self.assertGreater(self.sink.dropped, 0)

    def test_two_outputs_one_rpc_sample(self):
        exporter = Exporter(dict(influxdb=self.config,
            mqtt=dict(host='127.0.0.1', tls=False)))
        exporter.client = Mock()
        exporter.connected.set()
        exporter.interval = .01
        calls, published = [], []
        def sample(history):
            calls.append(1)
            if len(calls) == 3:
                exporter.stop.set()
            return dict(SAMPLE, sampled_at=SAMPLE['sampled_at'] + 30 * len(calls))
        exporter.rpc.sample = sample
        exporter.rpc.security = lambda: {}
        exporter.publish = lambda suffix, payload: published.append(suffix)
        exporter.run()
        self.assertEqual(len(calls), 3)
        self.assertEqual(published.count('state'), 1)
        self.assertEqual(self.server.received[-1][2].count(b'\n'), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
