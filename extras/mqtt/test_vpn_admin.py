import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

from admin import Supervisor, read, write
from vpn_admin import defaults, validate


class VpnAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Supervisor(self.temp.name)
        self.config = defaults(self.temp.name)
        self.config.update(run_user=next(user.pw_name for user in pwd.getpwall() if user.pw_uid == 1000),
            daemon='/bin/true', username='vpn-user', password='vpn-secret',
            openvpn_profile='client\nproto udp\nremote vpn.example.test 1194\n<ca>\ntest-ca\n</ca>\n')

    def tearDown(self):
        self.temp.cleanup()

    def job(self, action, config):
        write(self.manager.request, dict(job_id='vpn-test', target='vpn', action=action, configuration=config))
        self.manager.process()
        return read(self.manager.result)

    def test_save_private_json_and_validate_no_network(self):
        result = self.job('save', self.config)
        self.assertEqual(result['state'], 'saved')
        self.assertTrue(result['restart_required'])
        saved = Path(self.temp.name)/'vpn.json'
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(read(saved), self.config)
        self.assertFalse(self.manager.config.exists())
        checked = self.job('test', self.config)
        self.assertEqual(checked['checks']['transport'], 'udp')
        self.assertNotIn('vpn-secret', json.dumps(checked))
        self.assertFalse((Path(self.temp.name)/'vpn-credentials.txt').exists())

    def test_bad_profile_preserves_previous(self):
        self.job('save', self.config)
        modified = dict(self.config, openvpn_profile=self.config['openvpn_profile']+'up /tmp/hook\n')
        self.assertEqual(self.job('save', modified)['state'], 'error')
        self.assertEqual(read(Path(self.temp.name)/'vpn.json'), self.config)

    def test_invalid_user_directory_and_credentials(self):
        for changes in ({'run_user': 'root'}, {'config_dir': '/elsewhere'}, {'password': 'one\ntwo'},
                        {'provider': 'unsupported'}, {'rpc_port': 1}, {'dns': ['127.0.0.1']}):
            self.assertEqual(self.job('save', dict(self.config, **changes))['state'], 'error')

    def test_unchanged_save_does_not_replace_file(self):
        self.job('save', self.config)
        path=Path(self.temp.name)/'vpn.json'
        inode=path.stat().st_ino
        self.job('save', self.config)
        self.assertEqual(path.stat().st_ino, inode)

    def test_connection_test_dispatch_does_not_save(self):
        with patch('vpn_admin.test_connection', return_value={'state': 'tested', 'elapsed_ms': 12,
                   'checks': {'authentication': 'ok', 'tunnel': 'ok'}}) as probe:
            result = self.job('connect_test', self.config)
            probe.assert_called_once_with(self.config, self.manager.directory)
        self.assertEqual(result['state'], 'tested')
        self.assertEqual(result['job_id'], 'vpn-test')
        self.assertFalse((Path(self.temp.name)/'vpn.json').exists())

    def test_environment_used_for_tests_but_not_persisted(self):
        self.manager.environ = {'TRANSMISSION_VPN_PASSWORD': 'env-only-secret',
                                'TRANSMISSION_VPN_START_TIMEOUT': '17'}
        self.assertEqual(self.job('save', self.config)['state'], 'saved')
        self.assertEqual(read(Path(self.temp.name)/'vpn.json'), self.config)
        with patch('vpn_admin.test_connection', return_value={'state': 'tested'}) as probe:
            result = self.job('connect_test', self.config)
            effective = probe.call_args.args[0]
            self.assertEqual(effective['password'], 'env-only-secret')
            self.assertEqual(effective['start_timeout'], 17)
        self.assertIn('TRANSMISSION_VPN_PASSWORD', result['vpn_environment_overrides'])
        self.assertNotIn('env-only-secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main(verbosity=2)
