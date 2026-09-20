import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from environment import FIELDS, overrides, resolve
from admin import Supervisor, read, write
from exporter import Exporter


class EnvironmentTests(unittest.TestCase):
    def test_precedence_types_and_no_mutation(self):
        config = {'interval_seconds': 30, 'mqtt': {'enabled': True, 'port': 1883}}
        effective = resolve(config, {'TRANSMISSION_TELEMETRY_INTERVAL_SECONDS': '60',
            'TRANSMISSION_MQTT_ENABLED': 'off', 'TRANSMISSION_MQTT_PORT': '8883'})
        self.assertEqual(effective['interval_seconds'], 60)
        self.assertIs(effective['mqtt']['enabled'], False)
        self.assertEqual(effective['mqtt']['port'], 8883)
        self.assertEqual(config['mqtt']['port'], 1883)
        self.assertEqual(resolve(config, {}), config)

    def test_credential_file_replaces_json_inline(self):
        for section, prefix, name in [('rpc', 'TRANSMISSION_TELEMETRY_RPC_', 'password'),
                                      ('mqtt', 'TRANSMISSION_MQTT_', 'password'),
                                      ('influxdb', 'TRANSMISSION_INFLUXDB_', 'token')]:
            env = {prefix + name.upper() + '_FILE': '/private/credential'}
            result = resolve({section: {name: 'old-secret'}}, env)[section]
            self.assertNotIn(name, result)
            self.assertEqual(result[name + '_file'], '/private/credential')
            result = resolve({section: {name + '_file': '/old'}}, {prefix + name.upper(): 'new'})[section]
            self.assertNotIn(name + '_file', result)
            self.assertEqual(result[name], 'new')
            with self.assertRaises(ValueError):
                resolve({}, dict(env, **{prefix + name.upper(): 'new'}))

    def test_invalid_values_never_expose_value(self):
        for name in ('TRANSMISSION_MQTT_PORT', 'TRANSMISSION_MQTT_ENABLED'):
            with self.assertRaises(ValueError) as context:
                resolve({}, {name: 'private-value'})
            self.assertNotIn('private-value', str(context.exception))
        result = resolve({'mqtt': {'password': 'old'}}, {'TRANSMISSION_MQTT_PASSWORD': ''})
        self.assertEqual(result['mqtt']['password'], '')

    def test_every_mapping_and_boolean_variants(self):
        for name, (path, kind) in FIELDS.items():
            value = '17' if kind is int else 'true' if kind is bool else 'value'
            result = resolve({}, {name: value})
            for part in path.split('.'):
                result = result[part]
            self.assertEqual(result, 17 if kind is int else True if kind is bool else 'value')
        for value in ('TRUE', 'true', 'yes', '1', 'on'):
            self.assertTrue(resolve({}, {'TRANSMISSION_MQTT_TLS': value})['mqtt']['tls'])
        for value in ('FALSE', 'false', 'no', '0', 'off'):
            self.assertFalse(resolve({}, {'TRANSMISSION_MQTT_TLS': value})['mqtt']['tls'])

    def test_save_and_test_use_env_but_json_and_results_do_not_store_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = Supervisor(folder)
            manager.environ = {'TRANSMISSION_MQTT_ENABLED': 'false',
                'TRANSMISSION_MQTT_PASSWORD': 'env-private', 'TRANSMISSION_TELEMETRY_INTERVAL_SECONDS': '90'}
            config = {'interval_seconds': 30, 'mqtt': {'enabled': False}}
            write(manager.request, dict(job_id='save', action='save', configuration=config))
            manager.process()
            self.assertEqual(read(manager.config), config)
            self.assertNotIn('env-private', manager.result.read_text())
            self.assertIn('TRANSMISSION_MQTT_PASSWORD', read(manager.result)['environment_overrides'])
            write(manager.request, dict(job_id='test', action='test', configuration=config))
            with patch('admin.test', return_value={'rpc': 'ok'}) as probe:
                manager.process()
                self.assertEqual(probe.call_args.args[0]['interval_seconds'], 90)
            manager.halt()

    def test_env_only_start_without_json(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = Supervisor(folder)
            manager.environ = {'TRANSMISSION_MQTT_ENABLED': 'false'}
            manager.stop.set()
            with patch.object(manager, 'start') as start:
                manager.run()
                start.assert_called_once_with({})
            self.assertFalse(manager.config.exists())

    def test_env_only_influx_constructs_without_files(self):
        config = resolve({}, {'TRANSMISSION_INFLUXDB_ENABLED': 'true',
            'TRANSMISSION_INFLUXDB_URL': 'http://127.0.0.1:1', 'TRANSMISSION_INFLUXDB_ORG': 'test',
            'TRANSMISSION_INFLUXDB_BUCKET': 'test', 'TRANSMISSION_INFLUXDB_TOKEN': 'private'})
        exporter = Exporter(config)
        self.assertIsNone(exporter.client)
        self.assertEqual(exporter.influx.headers['Authorization'], 'Token private')
        exporter.rpc.close()
        exporter.influx.connection.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
