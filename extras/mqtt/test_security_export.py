import json
import unittest
from unittest.mock import Mock
from security_export import SecurityExport
from exporter import Exporter


def row(stamp=1700000000000, message='event=rpc_auth_failed source=127.0.0.1'):
    return dict(time_ms=stamp, source='Security', message=message)


class SecurityExportTest(unittest.TestCase):
    def test_deduplicates_and_keeps_same_millisecond_events(self):
        export = SecurityExport()
        document = dict(entries=[row(), row()], vpn_entries=dict(entries=[row(message='event=http_request_rejected status=413 count=7')]))
        events = export.collect(document, mqtt=True)
        self.assertEqual(len(events), 3)
        self.assertEqual(len({e['event_id'] for e in events}), 3)
        self.assertEqual(len({e['timestamp_ns'] for e in events}), 3)
        self.assertEqual(export.collect(document, mqtt=True), [])
        self.assertEqual(len(export.pending), 3)
        batch, payload = export.batch()
        self.assertEqual([e['event_id'] for e in json.loads(payload)['events']], [e['event_id'] for e in batch])
        self.assertNotIn('timestamp_ns', json.loads(payload)['events'][0])
        export.acknowledge(3)
        self.assertFalse(export.pending)

    def test_allowlist_omits_sensitive_text(self):
        export = SecurityExport()
        message = 'event=p2p_message_rejected type=20 length=262145 password=TOPSECRET source=192.0.2.1'
        events = export.collect(dict(entries=[row(message=message), row(message='event=unknown token=TOPSECRET'),
                                              row(stamp=True), row(message='event=rpc_auth_failed\nAuthorization: TOPSECRET')]))
        encoded = json.dumps(events)
        self.assertNotIn('TOPSECRET', encoded)
        self.assertNotIn('192.0.2.1', encoded)
        self.assertEqual(next(e for e in events if e['event_code'] == 'p2p_message_rejected')['length'], 262145)
        self.assertEqual(len(events), 2)

    def test_outage_queue_and_seen_cache_are_bounded(self):
        export = SecurityExport()
        for i in range(10000):
            export.collect(dict(entries=[row(stamp=1700000000000+i)]), mqtt=True)
        self.assertEqual(len(export.pending), 256)
        self.assertEqual(export.dropped, 9744)
        self.assertEqual(len(export.seen), 4096)
        self.assertEqual(len(export.batch()[0]), 64)

    def test_mqtt_is_qos_one_and_not_retained(self):
        export = Exporter(dict(mqtt=dict(host='localhost', tls=False)))
        export.client = Mock()
        info = export.client.publish.return_value
        info.rc = export.mqtt.MQTT_ERR_SUCCESS
        info.is_published.return_value = True
        export.publish('security/events', '{}', retain=False)
        self.assertEqual(export.client.publish.call_args.kwargs, dict(qos=1, retain=False))
        export.rpc.close()


if __name__ == '__main__': unittest.main()
