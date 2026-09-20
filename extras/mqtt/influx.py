"""Bounded, batched InfluxDB v2 line-protocol output; Python standard library only."""
from collections import deque
import hashlib
import http.client
import logging
import math
import os
from pathlib import Path
import ssl
import threading
import time
import urllib.parse


def tag(value):
    value = str(value)
    if any(c in value for c in '\r\n\x00'):
        raise ValueError('Invalid Influx tag')
    return value.replace('\\', '\\\\').replace(' ', '\\ ').replace(',', '\\,').replace('=', '\\=')


def field(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int) and -(2**63) <= value < 2**63:
        return str(value) + 'i'
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n').replace('\x00', '') + '"'
    return None


def encode(sample, instance, directories=False):
    timestamp = int(sample['sampled_at']) * 1000000000
    if timestamp <= 0:
        raise ValueError('Invalid sample timestamp')
    def line(measurement, tags, values):
        fields = []
        for key, value in values.items():
            encoded = field(value)
            if encoded is not None:
                fields.append(key + '=' + encoded)
        return f'{measurement},instance={instance}{tags} ' + ','.join(fields) + f' {timestamp}\n'
    storage = sample['storage_status']
    values = {key: sample[key] for key in ('download_speed', 'upload_speed', 'torrent_count',
               'active_torrent_count', 'paused_torrent_count') if key in sample}
    for key in ('system_state', 'automatic', 'cache_reserved_bytes', 'cache_pending_bytes',
                'cache_capacity_bytes', 'cache_congested', 'cpu_wait_percent',
                'memory_wait_percent', 'io_wait_percent'):
        if key in storage:
            values[key] = float(storage[key]) if key.endswith('_wait_percent') else storage[key]
    for key in ('state', 'provider', 'protocol', 'kill_switch', 'received_bytes', 'sent_bytes',
                'received_packets', 'sent_packets', 'traffic_scope', 'traffic_sampled_at'):
        if key in sample['vpn_status']:
            values['vpn_' + key] = sample['vpn_status'][key]
    for key in ('downloaded_bytes', 'uploaded_bytes', 'seconds_active', 'session_count', 'files_added'):
        if key in sample['cumulative_stats']:
            values[key] = sample['cumulative_stats'][key]
    yield line('transmission', '', values).encode()
    if directories:
        for row in storage.get('directories', []):
            path = row['path']
            # Path hash and the bounded profile set distinguish directory groups.
            identifier = hashlib.sha256(path.encode()).hexdigest()[:32]
            values = {key: row[key] for key in ('path', 'profile', 'load', 'torrents', 'active')}
            yield line('transmission_directory', ',directory=' + identifier + ',profile_id=' + tag(row['profile']), values).encode()


class Influx:
    def __init__(self, config):
        parsed = urllib.parse.urlsplit(config['url'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Invalid Influx URL')
        self.instance = tag(config.get('instance', 'transmission'))
        self.directories = bool(config.get('include_directories', False))
        self.interval = float(config.get('flush_seconds', 60))
        self.limit = int(config.get('buffer_bytes', 262144))
        if not 10 <= self.interval <= 3600 or not 4096 <= self.limit <= 4194304 or not self.instance:
            raise ValueError('Invalid Influx batching configuration')
        self.path = parsed.path.rstrip('/') + '/api/v2/write?' + urllib.parse.urlencode(
            {'org': config['org'], 'bucket': config['bucket'], 'precision': 'ns'})
        token = config.get('token') or Path(os.path.expandvars(config['token_file'])).read_text().strip()
        if not token or '\n' in token or '\r' in token:
            raise ValueError('Invalid Influx token')
        self.headers = {'Authorization': 'Token ' + token, 'Content-Type': 'text/plain; charset=utf-8'}
        if parsed.scheme == 'https':
            self.connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=5,
                context=ssl.create_default_context(cafile=config.get('ca_file')))
        else:
            self.connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        self.queue = deque()
        self.size = 0
        self.dropped = 0
        self.condition = threading.Condition()
        self.stopping = False
        self.thread = None

    def add(self, sample):
        # Encode incrementally: even a large directory list cannot build an unbounded payload.
        self.add_lines(encode(sample, self.instance, self.directories))

    def add_security(self, events):
        def lines():
            for event in events:
                values = ','.join(key + '=' + field(value) for key, value in event.items()
                    if key not in ('timestamp_ns', 'event_code'))
                yield (f'transmission_security,instance={self.instance},event_code={tag(event["event_code"])} '
                       + values + f' {event["timestamp_ns"]}\n').encode()
        self.add_lines(lines())

    def add_lines(self, lines):
        for data in lines:
            with self.condition:
                if len(data) > self.limit:
                    self.dropped += 1
                    continue
                while self.queue and self.size + len(data) > self.limit:
                    removed = self.queue.popleft()
                    self.size -= len(removed)
                    self.dropped += removed.count(b'\n')
                self.queue.append(data)
                self.size += len(data)

    def start(self):
        self.thread = threading.Thread(target=self.run, name='influx-writer')
        self.thread.start()

    def write(self, payload):
        try:
            self.connection.request('POST', self.path, payload, self.headers)
            with self.connection.getresponse() as response:
                status = response.status
                raw = response.read(4097)
            if len(raw) > 4096:
                self.connection.close()
            return status == 204
        except (OSError, http.client.HTTPException):
            self.connection.close()
            return False

    def run(self):
        delay = self.interval
        failed = False
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopping, timeout=delay)
                    stopping = self.stopping
                    payload = b''.join(self.queue)
                    self.queue.clear()
                    self.size = 0
                if payload:
                    if self.write(payload):
                        delay, failed = self.interval, False
                    else:
                        if not failed:
                            logging.warning('InfluxDB unavailable; bounded retry buffer active')
                        failed = True
                        delay = min(max(300, self.interval), delay * 2)
                        with self.condition:
                            # Keep newer samples first when outage exceeds buffer capacity.
                            if self.size + len(payload) <= self.limit:
                                self.queue.appendleft(payload)
                                self.size += len(payload)
                            else:
                                self.dropped += payload.count(b'\n')
                if stopping:
                    break
        finally:
            self.connection.close()

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.thread:
            self.thread.join()
