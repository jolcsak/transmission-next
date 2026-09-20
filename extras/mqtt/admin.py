#!/usr/bin/env python3
"""Host-side telemetry configuration supervisor; same user/config directory as daemon."""
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import uuid

from exporter import Exporter, RPC
from environment import overrides, resolve
from vpn_admin import environment_overrides as vpn_overrides, resolve_environment as resolve_vpn


def read(path):
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Configuration exceeds 64 KiB')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Configuration must be a JSON object')
    return value


def write(path, document):
    data = json.dumps(document, ensure_ascii=False, indent=2).encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.telemetry-', delete=False) as stream:
        name = stream.name
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            os.unlink(name)
            raise
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def enabled(config):
    return (bool(config.get('mqtt')) and config['mqtt'].get('enabled', True)) or config.get('influxdb', {}).get('enabled', False)


def validate(config, construct=True):
    if set(config) - {'rpc', 'mqtt', 'influxdb', 'interval_seconds', 'heartbeat_seconds', 'include_history'}:
        raise ValueError('Unknown configuration field')
    for key in ('rpc', 'mqtt', 'influxdb'):
        if key in config and not isinstance(config[key], dict):
            raise ValueError('Invalid configuration section')
    for group, keys in ((config, ('include_history',)),
                        (config.get('mqtt', {}), ('enabled', 'tls')),
                        (config.get('influxdb', {}), ('enabled', 'include_directories'))):
        if any(key in group and not isinstance(group[key], bool) for key in keys):
            raise ValueError('Boolean setting required')
    interval = config.get('interval_seconds', 30)
    heartbeat = config.get('heartbeat_seconds', 300)
    if not isinstance(interval, (int, float)) or not 10 <= interval <= 3600:
        raise ValueError('Invalid sampling interval')
    if not isinstance(heartbeat, (int, float)) or not interval <= heartbeat <= 86400:
        raise ValueError('Invalid heartbeat interval')
    broker = config.get('mqtt', {})
    if broker and broker.get('enabled', True):
        if not isinstance(broker.get('host'), str) or not broker['host'].strip():
            raise ValueError('MQTT host required')
        if not 1 <= int(broker.get('port', 8883 if broker.get('tls', True) else 1883)) <= 65535:
            raise ValueError('Invalid MQTT port')
    influx = config.get('influxdb', {})
    if influx.get('enabled', False):
        for key in ('org', 'bucket'):
            if not isinstance(influx.get(key), str) or not influx[key].strip():
                raise ValueError('Influx organization and bucket required')
    if construct and enabled(config):
        candidate = Exporter(config)
        candidate.rpc.close()
        if candidate.influx:
            candidate.influx.connection.close()


def test(config):
    validate(config, construct=False)
    result = {}
    rpc = RPC(config.get('rpc', {}))
    try:
        rpc.sample(False)
        result['rpc'] = 'ok'
    except Exception:
        result['rpc'] = 'failed'
    finally:
        rpc.close()
    if not enabled(config):
        return result
    candidate_config = copy.deepcopy(config)
    if candidate_config.get('mqtt', {}).get('enabled', True) and candidate_config.get('mqtt'):
        candidate_config['mqtt']['client_id'] = 'transmission-test-' + uuid.uuid4().hex[:16]
    candidate = Exporter(candidate_config)
    try:
        if candidate.client:
            client = candidate.client
            client.will_clear()
            try:
                client.connect_async(candidate.host, candidate.port, keepalive=15)
                client.loop_start()
                if not candidate.connected.wait(8):
                    raise ValueError('MQTT unavailable')
                info = client.publish(candidate.prefix + '/test', '{"test":true}', qos=1, retain=False)
                info.wait_for_publish(timeout=5)
                result['mqtt'] = 'ok' if info.is_published() else 'failed'
            except Exception:
                result['mqtt'] = 'failed'
            finally:
                client.disconnect()
                client.loop_stop()
        if candidate.influx:
            payload = f'transmission_connection_test,instance={candidate.influx.instance} success=true {int(time.time())}\n'.encode()
            result['influxdb'] = 'ok' if candidate.influx.write(payload) else 'failed'
    finally:
        candidate.rpc.close()
        if candidate.influx:
            candidate.influx.connection.close()
    return result


class Supervisor:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.config = self.directory / 'telemetry.json'
        self.request = self.directory / 'telemetry-request.json'
        self.result = self.directory / 'telemetry-result.json'
        self.exporter = None
        self.thread = None
        self.stop = threading.Event()
        self.environ = dict(os.environ)
        self.applied = None
        self.credentials = None

    def credential_state(self, config):
        state = []
        if not enabled(config):
            return state
        for section in ('rpc', 'mqtt', 'influxdb'):
            settings = config.get(section, {})
            if section == 'mqtt' and (not settings or not settings.get('enabled', True)):
                continue
            if section == 'influxdb' and not settings.get('enabled', False):
                continue
            files = {'rpc': ('password_file',), 'mqtt': ('password_file', 'ca_file', 'cert_file', 'key_file'),
                     'influxdb': ('token_file', 'ca_file')}[section]
            for key, value in sorted(settings.items()):
                if key in files and value:
                    if key == 'password_file' and not settings.get('username'):
                        continue
                    if key == 'key_file' and not settings.get('cert_file'):
                        continue
                    if key in ('password_file', 'token_file') and settings.get(key.removesuffix('_file')):
                        continue
                    if key in ('ca_file', 'cert_file', 'key_file'):
                        if section == 'mqtt' and not settings.get('tls', True):
                            continue
                        if section == 'influxdb' and not settings.get('url', '').startswith('https:'):
                            continue
                    path = os.path.expandvars(value)
                    info = Path(path).stat()
                    state.append((section, key, path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        return state

    def unchanged(self, config, credentials):
        healthy = self.thread.is_alive() if self.thread else not enabled(config)
        return healthy and config == self.applied and credentials == self.credentials

    def halt(self):
        if self.exporter:
            self.exporter.stop.set()
            self.thread.join(timeout=20)
            if self.thread.is_alive():
                raise RuntimeError('Previous exporter is still stopping')
        self.exporter = self.thread = None

    def start(self, config):
        config = resolve(config, self.environ)
        validate(config, construct=False)
        credentials = self.credential_state(config)
        if self.unchanged(config, credentials):
            return False
        candidate = Exporter(config) if enabled(config) else None
        self.halt()
        if candidate:
            self.exporter = candidate
            self.thread = threading.Thread(target=self.exporter.run, daemon=True)
            self.thread.start()
        self.applied, self.credentials = config, credentials
        return True

    def write_result(self, result):
        result['vpn_environment_overrides'] = vpn_overrides(self.environ)
        write(self.result, result)

    def process(self):
        if not self.request.exists():
            return
        job = {}
        try:
            job = read(self.request)
            config = job['configuration']
            if job.get('target') == 'vpn':
                from vpn_admin import validate as validate_vpn
                effective_vpn = resolve_vpn(config, self.environ)
                checks = validate_vpn(effective_vpn, self.directory)
                if job['action'] == 'save':
                    destination = self.directory / 'vpn.json'
                    if not destination.exists() or read(destination) != config:
                        write(destination, config)
                    result = {'state': 'saved', 'restart_required': True}
                elif job['action'] == 'test':
                    result = {'state': 'tested', 'checks': checks}
                elif job['action'] == 'connect_test':
                    from vpn_admin import test_connection
                    result = test_connection(effective_vpn, self.directory)
                else:
                    raise ValueError('Invalid action')
                result['job_id'] = job.get('job_id')
                self.write_result(result)
                self.request.unlink(missing_ok=True)
                return
            effective = resolve(config, self.environ)
            validate(effective, construct=False)
            if job['action'] == 'save':
                previous = read(self.config) if self.config.exists() else None
                changed = previous != config
                if changed:
                    write(self.config, config)
                try:
                    self.start(config)
                except Exception:
                    if previous is not None and changed:
                        write(self.config, previous)
                        try:
                            self.start(previous)
                        except Exception:
                            pass
                    elif previous is None:
                        self.config.unlink(missing_ok=True)
                    raise
                result = {'state': 'saved'}
            elif job['action'] == 'test':
                checks = test(effective)
                result = {'state': 'tested', 'checks': checks}
            else:
                raise ValueError('Invalid action')
        except Exception:
            # Do not expose credentials, remote response bodies or certificate paths.
            result = {'state': 'error', 'message': 'Invalid configuration, missing dependency/credential, or exporter could not be restarted.'}
        result['job_id'] = job.get('job_id')
        result['environment_overrides'] = overrides(self.environ)
        self.write_result(result)
        self.request.unlink(missing_ok=True)

    def run(self):
        from vpn_admin import defaults as vpn_defaults
        write(self.directory / 'vpn-defaults.json', vpn_defaults(self.directory))
        try:
            config = read(self.config) if self.config.exists() else {}
            self.start(config)
            self.write_result({'state': 'ready', 'environment_overrides': overrides(self.environ)})
        except Exception:
            self.write_result({'state': 'error', 'message': 'Stored configuration or environment could not be started.',
                                'environment_overrides': overrides(self.environ)})
        try:
            while not self.stop.is_set():
                self.process()
                self.stop.wait(2)
        finally:
            self.halt()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', default=os.environ.get('TRANSMISSION_CONFIG_DIR'))
    args = parser.parse_args()
    if not args.config_dir:
        parser.error('--config-dir or TRANSMISSION_CONFIG_DIR is required')
    supervisor = Supervisor(args.config_dir)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: supervisor.stop.set())
    supervisor.run()
