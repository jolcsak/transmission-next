import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

import controller
from vpn_environment import resolve, overrides


class EnvironmentTests(unittest.TestCase):
    def test_types_and_source_unchanged(self):
        source = {'dns': ['1.1.1.1'], 'rpc_port': 9091}
        env = {'TRANSMISSION_VPN_DNS': '9.9.9.9, 1.0.0.1', 'TRANSMISSION_VPN_RPC_PORT': '9191'}
        self.assertEqual(resolve(source, env), {'dns': ['9.9.9.9', '1.0.0.1'], 'rpc_port': 9191})
        self.assertEqual(source, {'dns': ['1.1.1.1'], 'rpc_port': 9091})

    def test_file_replaces_inline(self):
        resolved = resolve({'username': 'old', 'password': 'old', 'openvpn_profile': 'old'},
            {'TRANSMISSION_VPN_CREDENTIALS_FILE': '/private/auth', 'TRANSMISSION_VPN_OPENVPN_CONFIG': '/private/profile'})
        self.assertEqual(resolved, {'credentials_file': '/private/auth', 'openvpn_config': '/private/profile'})

    def test_inline_replaces_files_and_partial_password_override(self):
        self.assertEqual(resolve({'credentials_file': '/old', 'openvpn_config': '/old'},
            {'TRANSMISSION_VPN_USERNAME': 'user', 'TRANSMISSION_VPN_PASSWORD': 'secret',
             'TRANSMISSION_VPN_OPENVPN_PROFILE': 'profile'}),
            {'username': 'user', 'password': 'secret', 'openvpn_profile': 'profile'})
        self.assertEqual(resolve({'username': 'user', 'password': 'old'},
                         {'TRANSMISSION_VPN_PASSWORD': 'new'})['username'], 'user')

    def test_conflicting_sources_and_invalid_integer_do_not_expose_values(self):
        for env in ({'TRANSMISSION_VPN_CREDENTIALS_FILE': 'sensitive', 'TRANSMISSION_VPN_PASSWORD': 'sensitive'},
                    {'TRANSMISSION_VPN_OPENVPN_CONFIG': 'sensitive', 'TRANSMISSION_VPN_OPENVPN_PROFILE': 'sensitive'},
                    {'TRANSMISSION_VPN_START_TIMEOUT': 'sensitive'}):
            with self.assertRaises(ValueError) as error:
                resolve({}, env)
            self.assertNotIn('sensitive', str(error.exception))

    def test_controller_loads_overlay_once_and_validates(self):
        config = dict(provider='purevpn', openvpn_profile='fixture', username='user', password='old',
            run_user=pwd.getpwuid(1000).pw_name, daemon='/bin/true', config_dir='/tmp',
            download_dir='/tmp', dns=['1.1.1.1'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'vpn.json';path.write_text(json.dumps(config))
            parsed, _ = controller.load_config(path, environ={'TRANSMISSION_VPN_PASSWORD': 'new'})
            self.assertEqual(parsed['password'], 'new')
            self.assertEqual(json.loads(path.read_text()), config)
            with self.assertRaises(ValueError):
                controller.load_config(path, environ={'TRANSMISSION_VPN_START_TIMEOUT': '0'})
            with patch.dict(os.environ, {'TRANSMISSION_VPN_PASSWORD': 'ambient'}):
                self.assertEqual(controller.load_config(path)[0]['password'], 'old')

    def test_metadata_only_names(self):
        self.assertEqual(overrides({'TRANSMISSION_VPN_PASSWORD': 'secret', 'OTHER': 'x'}),
                         ['TRANSMISSION_VPN_PASSWORD'])


if __name__ == '__main__':
    unittest.main()
