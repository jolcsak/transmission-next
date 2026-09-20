"""Regression tests for secret-free Docker startup diagnostics."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('entrypoint', Path(__file__).with_name('entrypoint.py'))
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        defaults = self.root / 'extras/auto'
        defaults.mkdir(parents=True)
        (defaults / 'settings.json').write_text('{}')
        self.addCleanup(patch.stopall)
        patch.object(entry, 'CONFIG', self.config).start()
        patch.object(entry, 'ROOT', self.root).start()
        patch.object(entry.os, 'chown').start()
        patch.object(entry.os, 'geteuid', return_value=0).start()
        self.original_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.original_umask)

    def failure(self, env, expected):
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(entry.StartupError) as caught:
                entry.initialize()
        self.assertEqual(caught.exception.code, expected)
        self.assertIn(expected, entry.failure_message(caught.exception))
        self.assertFalse(entry.CHILDREN)
        return entry.failure_message(caught.exception)

    def test_missing_rpc_password(self):
        settings = {}
        output = io.StringIO()
        with patch('sys.stdout', output):
            entry.initial_password(settings)
            second = {}
            entry.initial_password(second)
        password = settings['rpc_password']
        self.assertGreaterEqual(len(password), 24)
        self.assertEqual(settings, second)
        self.assertNotIn(password, output.getvalue())
        self.assertEqual((self.config/'rpc-initial-password.txt').stat().st_mode & 0o777, 0o600)

    def test_short_password_not_logged(self):
        message = self.failure({'RPC_PASSWORD': 'private'}, 'rpc_password_invalid')
        self.assertNotIn('private', message)

    def test_conflicting_secret_sources(self):
        message = self.failure({'RPC_PASSWORD': 'never-log-this', 'RPC_PASSWORD_FILE': '/secret-location'},
                               'conflicting_secret_sources')
        self.assertNotIn('never-log-this', message)
        self.assertNotIn('/secret-location', message)

    def test_unreadable_secret(self):
        message = self.failure({'RPC_PASSWORD_FILE': str(self.root/'private-file')}, 'secret_file_unreadable')
        self.assertNotIn('private-file', message)

    def test_invalid_json(self):
        (self.config/'settings.json').write_text('{"password":"SECRET-MARKER", broken')
        message = self.failure({}, 'invalid_json')
        self.assertNotIn('SECRET-MARKER', message)

    def test_json_array_rejected(self):
        (self.config/'settings.json').write_text('["SECRET-MARKER"]')
        self.failure({}, 'invalid_json_object')

    def test_numeric_password_rejected(self):
        (self.config/'settings.json').write_text('{"rpc-password":123456789}')
        self.failure({}, 'rpc_password_type')

    def test_invalid_vpn_switch(self):
        self.failure({'RPC_PASSWORD': 'fixture-password', 'VPN_ENABLED': 'invalid'}, 'vpn_enabled_invalid')

    def test_vpn_default_auto_without_config(self):
        self.assertFalse(entry.vpn_requested({}, {}))
        self.assertFalse(entry.vpn_requested(dict(provider='purevpn', openvpn_config='', credentials_file='',
                                                 daemon='/old/bin', dns=['1.1.1.1']), {}))

    def test_vpn_explicit_modes(self):
        self.assertTrue(entry.vpn_requested({}, {'VPN_ENABLED': 'true'}))
        self.assertFalse(entry.vpn_requested({'openvpn_config': '/config/vpn.ovpn'}, {'VPN_ENABLED': 'false'}))

    def test_missing_tun_device_is_created_with_standard_device_number(self):
        path = Mock()
        path.parent = Mock()
        path.exists.return_value = False
        path.is_symlink.return_value = False
        path.parent.lstat.return_value = Mock(st_mode=stat.S_IFDIR | 0o755)
        path.lstat.return_value = Mock(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(10, 200))
        with patch.object(entry.os, 'mknod') as mknod, patch.object(entry.os, 'chmod'):
            entry.ensure_tun_device(path)
        mknod.assert_called_once_with(path, stat.S_IFCHR | 0o600, os.makedev(10, 200))

    def test_wrong_tun_device_is_rejected(self):
        path = Mock()
        path.parent = Mock()
        path.exists.return_value = True
        path.parent.lstat.return_value = Mock(st_mode=stat.S_IFDIR | 0o755)
        path.lstat.return_value = Mock(st_mode=stat.S_IFREG | 0o600, st_rdev=0)
        with self.assertRaises(entry.StartupError) as caught:
            entry.ensure_tun_device(path)
        self.assertEqual(caught.exception.code, 'tun_device_invalid')

    def test_partial_vpn_config_remains_protected(self):
        for config in ({'openvpn_config': '/missing.ovpn'}, {'username': 'fixture'}, {'password': 'fixture'}):
            self.assertTrue(entry.vpn_requested(config, {}))
        for name in ('USERNAME_FILE', 'PASSWORD_FILE', 'OPENVPN_CONFIG', 'OPENVPN_PROFILE'):
            self.assertTrue(entry.vpn_requested({}, {'TRANSMISSION_VPN_' + name: 'fixture'}))
        self.assertFalse(entry.vpn_requested({}, {'TRANSMISSION_VPN_PROVIDER': 'purevpn'}))

    def test_blank_optional_secrets_are_unset(self):
        with patch.dict(os.environ, {'RPC_PASSWORD': '', 'RPC_PASSWORD_FILE': ''}, clear=True):
            self.assertIsNone(entry.secret('RPC_PASSWORD'))

    def test_storage_profiles_from_json_and_environment(self):
        (self.config/'storage-profiles.json').write_text(json.dumps({
            'default': 'ssd',
            'directories': {'/downloads': 'hdd', '/fast': 'auto'},
        }))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(entry.disk_profile_rules(), '/downloads=hdd;*=ssd')
        with patch.dict(os.environ, {
                'STORAGE_PROFILE_DEFAULT': 'auto',
                'STORAGE_PROFILE_RULES': '/downloads=hdd;/movies=ssd'}, clear=True):
            self.assertEqual(entry.disk_profile_rules(), '/downloads=hdd;/movies=ssd')

    def test_invalid_storage_profiles_are_rejected(self):
        (self.config/'storage-profiles.json').write_text('{"directories":{"relative":"hdd"}}')
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(entry.StartupError) as caught:
                entry.disk_profile_rules()
        self.assertEqual(caught.exception.code, 'storage_profile_rule_invalid')

    def test_bootstrap_symlink_rejected(self):
        target = self.root/'private'
        target.write_text('fixture-password')
        (self.config/'rpc-initial-password.txt').symlink_to(target)
        with self.assertRaises(OSError):
            entry.initial_password({})

    def test_incomplete_tls_pair(self):
        (self.config/'tls').mkdir()
        (self.config/'tls/server.crt').write_text('fixture')
        self.failure({'RPC_PASSWORD': 'fixture-password', 'VPN_ENABLED': 'false'}, 'tls_pair_missing')

    def test_utf8_bom_json_and_secret_supported(self):
        path = self.root/'bom.json'
        path.write_text('{"enabled":true}', encoding='utf-8-sig')
        self.assertEqual(entry.read(path), {'enabled': True})
        path.write_text('fixture-password\r\n', encoding='utf-8-sig')
        with patch.dict(os.environ, {'RPC_PASSWORD_FILE': str(path)}, clear=True):
            self.assertEqual(entry.secret('RPC_PASSWORD'), 'fixture-password')

    def test_arbitrary_exception_text_never_logged(self):
        for error in (ValueError('SECRET-MARKER'), PermissionError('/SECRET-MARKER')):
            self.assertNotIn('SECRET-MARKER', entry.failure_message(error))


if __name__ == '__main__':
    unittest.main()
