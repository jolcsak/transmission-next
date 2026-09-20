"""Bounded, credential-free projection of retained security records."""
from collections import OrderedDict, deque
import hashlib
import json
import re

CODES = frozenset(('rpc_auth_failed', 'rpc_rate_limited', 'rpc_access_denied',
    'rpc_path_traversal_rejected', 'p2p_message_rejected', 'unsafe_file_path_rejected',
    'http_request_rejected'))


class SecurityExport:
    def __init__(self):
        self.seen = OrderedDict()
        self.pending = deque()
        self.dropped = 0
        self.timestamp_ns = 0

    def collect(self, document, mqtt=False):
        rows = document.get('entries', [])
        vpn = document.get('vpn_entries', {})
        rows = (rows if isinstance(rows, list) else [])[:512] + (
            vpn.get('entries', [])[:2000] if isinstance(vpn, dict) and isinstance(vpn.get('entries', []), list) else [])
        events = []
        occurrences = {}
        for row in rows:
            if not isinstance(row, dict) or row.get('source') != 'Security':
                continue
            message, stamp = row.get('message'), row.get('time_ms')
            if not isinstance(message, str) or len(message) > 4096 or type(stamp) is not int or not 0 < stamp < 9223372036854:
                continue
            match = re.match(r'event=([a-z0-9_]+)(?:\s|$)', message)
            if not match or match[1] not in CODES:
                continue
            fingerprint = hashlib.sha256(f'{stamp}\0{message}'.encode()).hexdigest()
            occurrence = occurrences.get(fingerprint, 0)
            occurrences[fingerprint] = occurrence + 1
            identifier = hashlib.sha256(f'{fingerprint}:{occurrence}'.encode()).hexdigest()
            if identifier in self.seen:
                self.seen.move_to_end(identifier)
                continue
            self.seen[identifier] = None
            if len(self.seen) > 4096:
                self.seen.popitem(last=False)
            event = dict(event_id=identifier, time_ms=stamp, event_code=match[1], level=3, count=1)
            # No free text, URL, source IP, path, token or request content leaves
            # the host. Only these bounded numeric facts are exported.
            for key in ('count', 'status', 'type', 'length', 'errno', 'previous_window_suppressed'):
                value = re.search(r'(?:^|[ ;])' + key + r'=(\d{1,19})(?:[ ;]|$)', message)
                if value and int(value[1]) < 2**63:
                    event[key] = int(value[1])
            events.append(event)
        events.sort(key=lambda event: (event['time_ms'], event['event_id']))
        for event in events:
            # Preserve multiple incidents within one millisecond without making
            # unique IDs high-cardinality Influx tags. Timestamp is kept for retries.
            self.timestamp_ns = max(event['time_ms'] * 1000000, self.timestamp_ns + 1)
            event['timestamp_ns'] = self.timestamp_ns
            if mqtt:
                if len(self.pending) >= 256:
                    self.pending.popleft()
                    self.dropped += 1
                self.pending.append(event)
        return events

    def batch(self):
        events = list(self.pending)[:64]
        public = [{key: value for key, value in event.items() if key != 'timestamp_ns'} for event in events]
        return events, json.dumps(dict(schema_version=1, events=public, dropped=self.dropped), separators=(',', ':'))

    def acknowledge(self, count):
        for _ in range(count):
            self.pending.popleft()
