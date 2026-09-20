"""Local integration tests: real Mosquitto + real daemon, in a network namespace."""
import copy
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

import paho.mqtt.client as mqtt
from exporter import Exporter, RPC


def wait_for(predicate, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for condition')


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net')
        subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.log = (cls.folder / 'process.log').open('w')
        (cls.folder / 'settings.json').write_text(json.dumps(dict(
            rpc_enabled=True, rpc_bind_address='127.0.0.1', rpc_port=19091,
            rpc_authentication_required=False, rpc_whitelist_enabled=False,
            dht_enabled=False, pex_enabled=False, lpd_enabled=False,
            port_forwarding_enabled=False, utp_enabled=False)))
        cls.daemon = subprocess.Popen([os.environ['TEST_DAEMON'], '-f', '-g', cls.temp.name],
                                      stdout=cls.log, stderr=cls.log)
        cls.broker = subprocess.Popen(['mosquitto', '-p', '19883'], stdout=cls.log, stderr=cls.log)
        cls.config = dict(rpc=dict(url='http://127.0.0.1:19091/transmission/rpc'),
                          mqtt=dict(host='127.0.0.1', port=19883, tls=False,
                                    topic_prefix='test/transmission'))
        cls.rpc = RPC(cls.config['rpc'])
        def ready():
            try:
                cls.rpc.sample(False)
                return True
            except (OSError, ValueError):
                return False
        wait_for(ready)
        cls.messages = []
        cls.sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='test-observer')
        cls.sub.on_connect = lambda c, *_: c.subscribe('test/transmission/#', qos=1)
        cls.sub.on_message = lambda c, u, m: cls.messages.append((m.topic, m.payload.decode()))
        cls.sub.connect('127.0.0.1', 19883)
        cls.sub.loop_start()
        wait_for(cls.sub.is_connected)

    @classmethod
    def tearDownClass(cls):
        cls.rpc.close()
        cls.sub.disconnect()
        cls.sub.loop_stop()
        for process in (cls.daemon, cls.broker):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        cls.log.close()
        cls.temp.cleanup()

    def test_api_schema_history_and_compatibility(self):
        plain = self.rpc.sample(False)
        self.assertEqual(plain['schema_version'], 1)
        self.assertNotIn('transfer_history', plain)
        self.assertIn('directories', plain['storage_status'])
        self.assertIn('system_state', plain['storage_status'])
        self.assertEqual(plain['vpn_status']['state'], 'unmanaged')
        connection = self.rpc.connection.sock
        self.assertIsNotNone(connection)
        self.assertIn('days', self.rpc.sample(True)['transfer_history'])
        self.assertIs(self.rpc.connection.sock, connection)
        request = urllib.request.Request(self.rpc.url, json.dumps(dict(
            jsonrpc='2.0', id=2, method='session_stats', params={})).encode(), self.rpc.headers)
        with urllib.request.urlopen(request) as response:
            old = json.load(response)['result']
        self.assertNotIn('storage_status', old)
        self.assertEqual(plain['torrent_count'], old['torrent_count'])

    def test_configuration_validation(self):
        for update in ({'interval_seconds': 0}, {'heartbeat_seconds': 5},
                       {'mqtt': dict(host='localhost', topic_prefix='bad/#')}):
            config = dict(self.config, **update)
            with self.assertRaises(ValueError):
                Exporter(config)

    def test_security_events_reach_broker_once_per_poll_history(self):
        from test_security_export import row
        exporter = Exporter(self.config)
        exporter.interval = .05
        exporter.rpc.security = lambda: dict(entries=[row()])
        start = len(self.messages)
        worker = threading.Thread(target=exporter.run)
        worker.start()
        try:
            wait_for(lambda: any(topic.endswith('/security/events') for topic, _ in self.messages[start:]))
            time.sleep(.3)
            messages = [json.loads(body) for topic, body in self.messages[start:] if topic.endswith('/security/events')]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]['events'][0]['event_code'], 'rpc_auth_failed')
        finally:
            exporter.stop.set()
            worker.join(timeout=10)
        self.assertFalse(worker.is_alive())

    def test_admin_rpc_save_test_and_busy(self):
        from admin import Supervisor, read
        def call(method, params):
            request = urllib.request.Request(self.rpc.url, json.dumps(dict(
                jsonrpc='2.0', id=22, method=method, params=params)).encode(), self.rpc.headers)
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        manager = Supervisor(self.folder)
        config = {'rpc': self.config['rpc'], 'mqtt': {'enabled': False}, 'influxdb': {'enabled': False}}
        params = {'job_id': 'rpc-save', 'action': 'save', 'configuration': json.dumps(config)}
        self.assertIn('result', call('telemetry_config_apply', params))
        self.assertIn('error', call('telemetry_config_apply', params))
        self.assertEqual(manager.request.stat().st_mode & 0o777, 0o600)
        manager.process()
        result = call('telemetry_config_get', {})['result']
        self.assertEqual(result['configuration'], config)
        self.assertEqual(result['job']['state'], 'saved')
        self.assertEqual(manager.config.parent, self.folder)
        params.update(action='test', job_id='rpc-test')
        call('telemetry_config_apply', params)
        manager.process()
        self.assertEqual(read(manager.result)['checks']['rpc'], 'ok')
        self.assertIn('error', call('telemetry_config_apply', dict(params, configuration='[]')))
        from admin import test as test_connections
        from test_influx import Handler
        import http.server
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        server.received, server.status = [], 204
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.start()
        try:
            probe = copy.deepcopy(self.config)
            probe['influxdb'] = dict(enabled=True, url=f'http://127.0.0.1:{server.server_port}',
                org='test', bucket='test', token='local-test-token')
            checks = test_connections(probe)
            self.assertEqual(checks, {'rpc': 'ok', 'mqtt': 'ok', 'influxdb': 'ok'})
            self.assertIn(b'transmission_connection_test', server.received[0][2])
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()
        manager.halt()

    def test_export_recovery_deduplication_and_shutdown(self):
        exporter = Exporter(self.config)
        # Shorten test sampling only after production configuration validation.
        exporter.interval = .1
        exporter.heartbeat = 60
        sample = self.rpc.sample(True)
        calls = []
        failure = threading.Event()
        def sample_rpc(history):
            calls.append(time.monotonic())
            if failure.is_set():
                raise ValueError('simulated daemon outage')
            result = copy.deepcopy(sample)
            result['sampled_at'] = time.time()
            result['current_stats']['seconds_active'] = len(calls)
            return result
        exporter.rpc.sample = sample_rpc
        start = len(self.messages)
        thread = threading.Thread(target=exporter.run)
        thread.start()
        try:
            wait_for(lambda: ('test/transmission/availability', 'online') in self.messages[start:])
            time.sleep(.5)
            states = [m for m in self.messages[start:] if m[0].endswith('/state')]
            self.assertEqual(len(states), 1)
            self.assertEqual(json.loads(states[0][1])['storage_status'], sample['storage_status'])
            failure.set()
            wait_for(lambda: ('test/transmission/availability', 'offline') in self.messages[start:])
            recovered = len(self.messages)
            failure.clear()
            wait_for(lambda: ('test/transmission/availability', 'online') in self.messages[recovered:])
            self.broker.terminate()
            self.broker.wait(timeout=10)
            wait_for(lambda: not exporter.connected.is_set())
            time.sleep(.2)
            count = len(calls)
            time.sleep(.5)
            self.assertEqual(len(calls), count, 'Broker outage must suspend RPC polling')
            recovered = len(self.messages)
            type(self).broker = subprocess.Popen(['mosquitto', '-p', '19883'], stdout=self.log, stderr=self.log)
            wait_for(lambda: ('test/transmission/availability', 'online') in self.messages[recovered:])
        finally:
            exporter.stop.set()
            thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        wait_for(lambda: self.messages[-1] == ('test/transmission/availability', 'offline'))

    def test_last_will_after_process_kill(self):
        path = self.folder / 'exporter.json'
        path.write_text(json.dumps(self.config))
        start = len(self.messages)
        proc = subprocess.Popen([os.sys.executable, str(Path(__file__).with_name('exporter.py')),
                                 '--config', str(path)], stdout=self.log, stderr=self.log)
        try:
            wait_for(lambda: ('test/transmission/availability', 'online') in self.messages[start:])
            start = len(self.messages)
            proc.kill()
            proc.wait(timeout=5)
            wait_for(lambda: ('test/transmission/availability', 'offline') in self.messages[start:])
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)

    def test_tls_and_broker_authentication(self):
        cert, key = self.folder / 'cert.pem', self.folder / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'],
                       check=True, stdout=self.log, stderr=self.log)
        password = self.folder / 'password'
        password.write_text('local-test-only\n')
        passwd = self.folder / 'broker.passwd'
        subprocess.run(['mosquitto_passwd', '-b', '-c', str(passwd), 'test', 'local-test-only'], check=True)
        conf = self.folder / 'tls.conf'
        conf.write_text(f'user root\nlistener 19884 127.0.0.1\ncertfile {cert}\nkeyfile {key}\n'
                        f'allow_anonymous false\npassword_file {passwd}\n')
        broker = subprocess.Popen(['mosquitto', '-c', str(conf)], stdout=self.log, stderr=self.log)
        exporter = None
        thread = None
        subscriber = None
        received = []
        try:
            def ready():
                try:
                    with socket.create_connection(('127.0.0.1', 19884), timeout=.2):
                        return True
                except OSError:
                    return False
            wait_for(ready)
            with socket.create_connection(('127.0.0.1', 19884)) as raw:
                with self.assertRaises(ssl.SSLCertVerificationError):
                    ssl.create_default_context().wrap_socket(raw, server_hostname='127.0.0.1')
            subscriber = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='tls-observer')
            subscriber.tls_set(ca_certs=str(cert))
            subscriber.username_pw_set('test', 'local-test-only')
            subscriber.on_connect = lambda c, *_: c.subscribe('test/tls/#', qos=1)
            subscriber.on_message = lambda c, u, m: received.append((m.topic, m.payload.decode()))
            subscriber.connect('127.0.0.1', 19884)
            subscriber.loop_start()
            wait_for(subscriber.is_connected)
            config = copy.deepcopy(self.config)
            config['mqtt'].update(port=19884, tls=True, ca_file=str(cert), username='test',
                                  password_file=str(password), topic_prefix='test/tls')
            exporter = Exporter(config)
            thread = threading.Thread(target=exporter.run)
            thread.start()
            wait_for(lambda: ('test/tls/availability', 'online') in received)
            self.assertTrue(any(topic.endswith('/state') for topic, _ in received))
        finally:
            if exporter:
                exporter.stop.set()
                thread.join(timeout=12)
            if subscriber:
                subscriber.disconnect()
                subscriber.loop_stop()
            broker.terminate()
            broker.wait(timeout=10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
