#!/usr/bin/env python3
"""Optional, host-side Transmission telemetry exporter. Requires paho-mqtt 2.1."""
import argparse
import base64
import http.client
import json
import logging
import os
import signal
import ssl
import threading
import time
import urllib.parse
from pathlib import Path

from influx import Influx
from environment import resolve
from security_export import SecurityExport

LIMIT = 1024 * 1024


def secret(path):
    return Path(os.path.expandvars(path)).read_text().rstrip('\r\n')


class RPC:
    def __init__(self, config):
        self.url = config.get('url', 'http://127.0.0.1:9091/transmission/rpc')
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
            raise ValueError('Invalid RPC URL')
        self.headers = {'Content-Type': 'application/json'}
        if config.get('username'):
            password = config.get('password') or secret(config['password_file'])
            token = base64.b64encode((config['username'] + ':' + password).encode()).decode()
            self.headers['Authorization'] = 'Basic ' + token
        # Direct persistent connection: no environment proxies or redirects.
        connection = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        self.connection = connection(parsed.hostname, parsed.port, timeout=5)
        self.path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
        self.bodies = {history: json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'telemetry_get',
                       'params': {'include_history': history}}, separators=(',', ':')).encode()
                       for history in (False, True)}

    def close(self):
        self.connection.close()

    def request(self, body):
        for attempt in range(2):
            try:
                self.connection.request('POST', self.path, body, self.headers)
                with self.connection.getresponse() as response:
                    raw = response.read(LIMIT + 1)
                    status = response.status
                    token = response.getheader('X-Transmission-Session-Id')
                if len(raw) > LIMIT:
                    raise ValueError('RPC response too large')
                if status == 409 and token and len(token) <= 256 and not attempt:
                    self.headers['X-Transmission-Session-Id'] = token
                    continue
                if status != 200:
                    raise ValueError('RPC request failed')
                result = json.loads(raw)['result']
                if not isinstance(result, dict):
                    raise ValueError('Malformed RPC result')
                return result
            except (OSError, http.client.HTTPException):
                self.close()
                if attempt:
                    raise ValueError('RPC connection failed') from None
            except (ValueError, KeyError, TypeError):
                self.close()
                raise
        raise ValueError('RPC session negotiation failed')

    def sample(self, history):
        result = self.request(self.bodies[history])
        if result.get('schema_version') != 1:
            raise ValueError('Unsupported telemetry schema')
        for key in ('storage_status', 'vpn_status', 'current_stats', 'cumulative_stats'):
            if not isinstance(result.get(key), dict):
                raise ValueError('Malformed telemetry')
        if history and not isinstance(result.get('transfer_history'), dict):
            raise ValueError('Malformed telemetry history')
        return result

    def security(self):
        return self.request(b'{"jsonrpc":"2.0","id":2,"method":"log_get","params":{}}')


def comparable(sample, history):
    """Compare values directly; serialize only when actually publishing."""
    result = {k: v for k, v in sample.items()
              if k not in ('sampled_at', 'current_stats', 'cumulative_stats', 'transfer_history')}
    if 'vpn_status' in result:
        result['vpn_status'] = {k: v for k, v in result['vpn_status'].items() if k != 'traffic_sampled_at'}
    for key in ('current_stats', 'cumulative_stats'):
        result[key] = {k: v for k, v in sample.get(key, {}).items() if k != 'seconds_active'}
    if history:
        result['transfer_history'] = {k: v for k, v in sample.get('transfer_history', {}).items() if k != 'now'}
    return result


class Exporter:
    def __init__(self, config):
        self.interval = float(config.get('interval_seconds', 30))
        self.heartbeat = float(config.get('heartbeat_seconds', 300))
        if not 10 <= self.interval <= 3600 or not self.interval <= self.heartbeat <= 86400:
            raise ValueError('Invalid sample/heartbeat interval')
        self.history = bool(config.get('include_history', False))
        self.influx = Influx(config['influxdb']) if config.get('influxdb', {}).get('enabled', False) else None
        self.rpc = RPC(config.get('rpc', {}))
        self.security = SecurityExport()
        self.security_failed = False
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.fresh_connection = threading.Event()
        self.client = None
        broker = config.get('mqtt', {})
        if not broker or not broker.get('enabled', True):
            if self.influx is None:
                raise ValueError('Enable at least one telemetry output')
            return
        import paho.mqtt.client as mqtt
        self.mqtt = mqtt
        self.prefix = broker.get('topic_prefix', 'transmission').rstrip('/')
        if not self.prefix or any(c in self.prefix for c in '#+\x00') or len(self.prefix.encode()) > 512:
            raise ValueError('Invalid MQTT topic prefix')
        self.host = broker['host']
        self.port = int(broker.get('port', 8883 if broker.get('tls', True) else 1883))
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=broker.get('client_id', 'transmission-telemetry'),
                                  clean_session=True)
        self.client.max_queued_messages_set(2)
        self.client.max_inflight_messages_set(1)
        self.client.reconnect_delay_set(5, 300)
        if broker.get('tls', True):
            context = ssl.create_default_context(cafile=broker.get('ca_file'))
            if broker.get('cert_file'):
                context.load_cert_chain(broker['cert_file'], broker.get('key_file'))
            self.client.tls_set_context(context)
        if broker.get('username'):
            password = broker.get('password') or secret(broker['password_file'])
            self.client.username_pw_set(broker['username'], password)
        self.client.will_set(self.prefix + '/availability', 'offline', qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, client, userdata, flags, reason, properties):
        if not reason.is_failure:
            self.fresh_connection.set()
            self.connected.set()

    def on_disconnect(self, client, userdata, flags, reason, properties):
        self.connected.clear()

    def publish(self, suffix, payload, retain=True):
        info = self.client.publish(self.prefix + '/' + suffix, payload, qos=1, retain=retain)
        if info.rc != self.mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError('MQTT unavailable')
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise RuntimeError('MQTT acknowledgement timeout')

    def run(self):
        if self.client:
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()
        if self.influx:
            self.influx.start()
        last, last_sent, health = None, 0, None
        failure_delay = self.interval
        try:
            while not self.stop.is_set():
                if not self.influx and not self.connected.is_set():
                    self.connected.wait(1)
                    continue
                if self.fresh_connection.is_set():
                    self.fresh_connection.clear()
                    last, health = None, None
                try:
                    # History serves MQTT only. Influx stores live counters and gauges.
                    sample = self.rpc.sample(self.history and self.connected.is_set())
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    if health != 'offline':
                        logging.warning('Telemetry RPC unavailable')
                        if self.connected.is_set():
                            try:
                                self.publish('availability', 'offline')
                            except (RuntimeError, ValueError):
                                pass
                    health, last = 'offline', None
                    self.stop.wait(failure_delay)
                    failure_delay = min(max(self.interval, 300), failure_delay * 2)
                    continue
                failure_delay = self.interval
                try:
                    events = self.security.collect(self.rpc.security(), mqtt=self.client is not None)
                    if self.influx:
                        self.influx.add_security(events)
                    self.security_failed = False
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    if not self.security_failed:
                        logging.warning('Security event RPC unavailable')
                    self.security_failed = True
                if self.influx:
                    try:
                        self.influx.add(sample)
                    except (ValueError, KeyError, TypeError):
                        logging.warning('Invalid Influx telemetry sample')
                if self.connected.is_set():
                    try:
                        signature = comparable(sample, self.history)
                        if signature != last or time.monotonic() - last_sent >= self.heartbeat:
                            self.publish('state', json.dumps(sample, separators=(',', ':')))
                            last, last_sent = signature, time.monotonic()
                        if health != 'online':
                            self.publish('availability', 'online')
                        health = 'online'
                        if self.security.pending:
                            events, payload = self.security.batch()
                            self.publish('security/events', payload, retain=False)
                            self.security.acknowledge(len(events))
                    except (OSError, ValueError, RuntimeError):
                        last = None
                        # An MQTT error must not slow Influx sampling.
                        if health != 'offline':
                            logging.warning('MQTT telemetry unavailable')
                        health = 'offline'
                self.stop.wait(self.interval)
        finally:
            self.rpc.close()
            if self.influx:
                self.influx.close()
            if self.client:
                if self.connected.is_set():
                    try:
                        self.publish('availability', 'offline')
                    except (RuntimeError, ValueError):
                        pass
                self.client.disconnect()
                self.client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=os.environ.get('TRANSMISSION_TELEMETRY_CONFIG'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        config = json.loads(Path(args.config).read_text()) if args.config else {}
        exporter = Exporter(resolve(config))
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, 'Invalid telemetry configuration or unreadable credential/certificate file\n')
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: exporter.stop.set())
    exporter.run()


if __name__ == '__main__':
    main()
