import dataclasses
import json
import os
from pathlib import Path
import pwd
import select
import subprocess
import tempfile
import unittest
from unittest import mock

import controller
from providers.purevpn import PureVPN, quote


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile = self.root / 'test.ovpn'
        self.base = 'client\nproto udp\nremote vpn.example.test 1194\n<ca>\ntest-ca\n</ca>\n'
        self.profile.write_text(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_sanitized_config(self):
        self.profile.write_text(self.base + 'dev tun\nauth-user-pass old.txt\nredirect-gateway def1\nverb 7\n')
        result = PureVPN.load(self.profile).render('192.0.2.2', '/run/private/auth', '/run/private/socket')
        self.assertIn('remote 192.0.2.2 1194', result)
        self.assertIn('remote-cert-tls server', result)
        self.assertIn('route-nopull', result)
        self.assertIn('script-security 1', result)
        self.assertNotIn('old.txt', result)
        self.assertNotIn('vpn.example.test', result)

    def test_forbidden_directives(self):
        for option in ['up /tmp/hook', 'down /tmp/hook', 'plugin /tmp/plugin.so',
                       'config /tmp/nested', 'management 127.0.0.1 1234',
                       'script-security 3', 'iproute /tmp/tool', 'daemon',
                       'user root', 'log /tmp/arbitrary', 'setenv PATH /tmp',
                       'http-proxy localhost 1234', 'route 1.2.3.4',
                       'remote-cert-tls client', '<connection>']:
            with self.subTest(option=option):
                self.profile.write_text(self.base + option + '\n')
                with self.assertRaises(ValueError):
                    PureVPN.load(self.profile)

    def test_external_certificates_become_inline(self):
        (self.root / 'ca.crt').write_text('sample-ca')
        (self.root / 'ta.key').write_text('sample-key')
        self.profile.write_text('remote vpn.example 443 tcp-client\nca ca.crt\ntls-auth ta.key 1\n')
        result = PureVPN.load(self.profile)
        self.assertEqual(result.protocol, 'tcp')
        self.assertIn('<ca>\nsample-ca\n</ca>', result.crypto)
        self.assertIn('key-direction 1', result.crypto)

    def test_invalid_profiles(self):
        for value in ['remote x\n', self.base + 'dev tap\n', self.base + 'proto udp6\n',
                      self.base.replace('1194', '70000'), self.base.replace('</ca>', ''),
                      self.base + '<ca>\nsecond\n</ca>\n']:
            with self.subTest(profile=value), self.assertRaises(ValueError):
                self.profile.write_text(value)
                PureVPN.load(self.profile)

    def test_legacy_compression_never_enabled_outbound(self):
        self.profile.write_text(self.base + 'comp-lzo yes\n')
        text = PureVPN.load(self.profile).render('192.0.2.2', '/auth', '/management')
        self.assertIn('comp-lzo no', text)
        self.assertNotIn('comp-lzo yes', text)
        self.assertIn('allow-compression asym', text)

    def test_quotes_do_not_create_options(self):
        self.assertEqual(quote('a"b\\c'), '"a\\"b\\\\c"')
        with self.assertRaises(ValueError):
            quote('a\nup /tmp/hook')

    def test_fast_io_only_for_udp_and_no_unnecessary_compression(self):
        for protocol in ('udp', 'tcp-client'):
            self.profile.write_text(self.base.replace('proto udp', 'proto ' + protocol))
            text = PureVPN.load(self.profile).render('192.0.2.2', '/auth', '/management')
            self.assertEqual('fast-io' in text.splitlines(), protocol == 'udp')
            self.assertIn('allow-compression no', text)

    def test_legacy_compression_framing_preserved(self):
        for option in ('compress', 'compress stub', 'compress stub-v2', 'comp-lzo no'):
            self.profile.write_text(self.base + option + '\n')
            text = PureVPN.load(self.profile).render('192.0.2.2', '/auth', '/management')
            if option.startswith('compress'):
                self.assertIn('compress stub-v2', text.splitlines())
            else:
                self.assertIn(option, text.splitlines())
            self.assertIn('allow-compression asym', text)

    def test_official_profile_managed_options(self):
        self.profile.write_text(self.base + 'compress\nroute-method exe\nroute-delay 0\n'
                                'route 0.0.0.0 0.0.0.0\nscript-security 2\ncipher AES-256-GCM\n')
        text = PureVPN.load(self.profile).render('192.0.2.2', '/auth', '/management')
        self.assertIn('compress stub-v2\n', text)
        self.assertIn('script-security 1', text)
        self.assertNotIn('script-security 2', text)
        self.assertNotIn('route-method', text)
        self.assertIn('data-ciphers ', text)

    def test_plaintext_cipher_refused(self):
        for option in ('cipher none', 'data-ciphers AES-256-GCM:none', 'data-ciphers-fallback none'):
            self.profile.write_text(self.base + option + '\n')
            with self.assertRaises(ValueError):
                PureVPN.load(self.profile)

    def test_bootstrap_resolves_only_ipv4(self):
        with mock.patch('socket.getaddrinfo', return_value=[(2, 2, 17, '', ('192.0.2.2', 1194))]) as resolve:
            self.assertEqual(PureVPN.load(self.profile).resolve(), '192.0.2.2')
            self.assertEqual(resolve.call_args.args[2], 2)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'config.json'
        self.config = dict(provider='purevpn', openvpn_config='/tmp/vpn.ovpn', credentials_file='/tmp/auth',
                           run_user='nobody', daemon='/bin/true', config_dir='/tmp/config', download_dir='/tmp/downloads',
                           dns=['1.1.1.1'])

    def tearDown(self):
        self.temp.cleanup()

    def load(self):
        self.path.write_text(json.dumps(self.config))
        return controller.load_config(self.path)

    def test_defaults(self):
        config, user = self.load()
        self.assertNotEqual(user.pw_uid, 0)
        self.assertEqual(config['host_ip'], '10.203.74.1')
        self.assertEqual(config['guest_ip'], '10.203.74.2')

    def test_invalid_values(self):
        for field, value in [('provider', '../evil'), ('run_user', 'root'), ('subnet', '0.0.0.0/30'),
                             ('subnet', '10.0.0.0/8'), ('rpc_port', True), ('rpc_port', 65536),
                             ('dns', ['127.0.0.1']), ('dns', ['::1']), ('dns', ['10.203.74.1']),
                             ('daemon', 'relative'), ('start_timeout', -1), ('plugin', '/tmp/script')]:
            old = self.config.copy()
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.config[field] = value
                self.load()
            self.config = old

    def test_credentials_private_and_not_in_diagnostics(self):
        secret = self.root / 'secret'
        secret.write_text('username\nfixture-password\n')
        secret.chmod(0o600)
        self.assertEqual(controller.credentials(secret), 'username\nfixture-password\n')
        secret.chmod(0o644)
        with self.assertRaises(ValueError) as error:
            controller.credentials(secret)
        self.assertNotIn('fixture-password', str(error.exception))
        secret.chmod(0o600)
        secret.write_text('only-one-line')
        with self.assertRaises(ValueError):
            controller.credentials(secret)

    def test_credentials_symlink_refused(self):
        secret = self.root / 'secret'
        secret.write_text('user\npassword\n')
        secret.chmod(0o600)
        link = self.root / 'link'
        link.symlink_to(secret)
        with self.assertRaises(OSError):
            controller.credentials(link)

    def test_firewall_has_no_general_established_bypass(self):
        config, _ = self.load()
        profile = dataclasses.make_dataclass('Profile', [('protocol', str), ('port', int)])('udp', 1194)
        rules = controller.namespace_rules(config, profile, '192.0.2.2')
        output = rules.split('chain input')[0]
        self.assertIn('policy drop', output)
        self.assertIn('meta skuid 0 ip daddr 192.0.2.2 udp dport 1194', output)
        self.assertIn('meta nfproto ipv6 counter drop', output)
        self.assertEqual(output.count('ct state established accept'), 1)
        self.assertIn('tcp sport 9091 ct state established accept', output)

    def test_existing_legacy_settings_keep_authentication(self):
        config, _ = self.load()
        user = pwd.getpwuid(os.getuid())
        config['config_dir'] = str(self.root/'state')
        config['download_dir'] = str(self.root/'downloads')
        Path(config['config_dir']).mkdir()
        Path(config['download_dir']).mkdir()
        settings = Path(config['config_dir'])/'settings.json'
        settings.write_text(json.dumps({'rpc-bind-address':'127.0.0.1', 'rpc-port':12345,
            'rpc-authentication-required':True, 'rpc-password':'fixture-hash',
            'auto-disk-profile-enabled':True, 'peer-limit-global':80}))
        controller.Supervisor(config,user,None).settings()
        saved = json.loads(settings.read_text())
        self.assertEqual(saved['rpc_bind_address'],'0.0.0.0')
        self.assertEqual(saved['rpc_port'],9091)
        self.assertTrue(saved['rpc_authentication_required'])
        self.assertEqual(saved['rpc_password'],'fixture-hash')
        self.assertTrue(saved['auto_disk_profile_enabled'])
        self.assertEqual(saved['peer_limit_global'],80)
        self.assertFalse(saved['port_forwarding_enabled'])
        self.assertNotIn('rpc-bind-address',saved)

    def test_public_vpn_log_is_bounded_and_secret_free(self):
        config, user = self.load()
        config['config_dir'] = str(self.root/'state')
        config['vpn_log_retention'] = 5
        Path(config['config_dir']).mkdir()
        profile = dataclasses.make_dataclass('Profile', [('protocol', str)])('udp')
        supervisor = controller.Supervisor(config, user, profile)
        runtime = self.root/'runtime'
        runtime.mkdir()
        with mock.patch.object(controller, 'RUNTIME', runtime):
            supervisor.event('connecting')
            supervisor.redactions = {'fixture-password', 'fixture-user'}
            supervisor.add_public_log('OpenVPN', 'password=fixture-password user:fixture-user')
            for number in range(70):
                supervisor.last_state = None
                supervisor.event('reconnecting', f'connection attempt {number}')
            supervisor.add_public_log('OpenVPN', 'password=fixture-password user:fixture-user')
            supervisor.persist_public_log()
            (runtime/'openvpn.log').write_text('TLS handshake complete\nAUTH token=fixture-password retry\n')
            supervisor.sync_public_log(force=True)
        saved = json.loads((Path(config['config_dir'])/'vpn-log.json').read_text())
        self.assertEqual(len(saved['entries']), 5)
        self.assertEqual(saved['entries'][-1]['source'], 'OpenVPN')
        self.assertIn('AUTH token=*** retry', [entry['message'] for entry in saved['entries']])
        self.assertNotIn('fixture-password', json.dumps(saved))
        self.assertNotIn('fixture-user', json.dumps(saved))
        self.assertEqual((Path(config['config_dir'])/'vpn-log.json').stat().st_mode & 0o777, 0o600)

    def test_child_exit_wakes_supervisor_without_timer(self):
        wake = controller.SignalWakeup()
        try:
            child = subprocess.Popen(['sleep','0.05'])
            self.assertTrue(select.select([wake.reader],[],[],2)[0])
            wake.drain()
            self.assertEqual(child.wait(timeout=1),0)
        finally:
            wake.close()

    def test_trim_logs_keeps_exact_tail_and_append_writer(self):
        config, user = self.load()
        with mock.patch.object(controller, 'RUNTIME', self.root):
            supervisor = controller.Supervisor(config, user, None)
        payload = bytes(range(256)) * 6144 + b'non-aligned-ending'
        for path in supervisor.log_paths:
            path.write_bytes(payload)
        with supervisor.log_paths[0].open('ab', buffering=0) as writer:
            supervisor.trim_logs()
            for path in supervisor.log_paths:
                self.assertEqual(path.read_bytes(), payload[-256*1024:])
            writer.write(b'after-trim')
        self.assertEqual(supervisor.log_paths[0].read_bytes(), payload[-256*1024:] + b'after-trim')

    def test_log_scan_one_stat_per_file_and_throttled(self):
        config, user = self.load()
        supervisor = controller.Supervisor(config, user, None)
        paths = [mock.Mock() for _ in range(3)]
        for path in paths:
            path.stat.return_value.st_size = 10
        supervisor.log_paths = paths
        with mock.patch.object(controller.time, 'monotonic', return_value=100):
            supervisor.trim_logs()
            supervisor.trim_logs()
        for path in paths:
            path.stat.assert_called_once_with()
            path.open.assert_not_called()

    def test_missing_logs_are_allowed(self):
        config, user = self.load()
        with mock.patch.object(controller, 'RUNTIME', self.root):
            supervisor = controller.Supervisor(config, user, None)
        supervisor.trim_logs()

    def test_dashboard_metadata_is_allowlisted_and_only_written_on_change(self):
        config, _ = self.load()
        user = pwd.getpwuid(os.getuid())
        config['config_dir'] = str(self.root / 'state')
        Path(config['config_dir']).mkdir()
        profile = mock.Mock(protocol='udp', hostname='hu-budapest-1.provider.example', port=15021)
        with mock.patch.object(controller, 'RUNTIME', self.root):
            supervisor = controller.Supervisor(config, user, profile)
            supervisor.settings_ready = True
            supervisor.server_endpoint = '203.0.113.10'
            supervisor.tunnel_ipv4 = '10.8.0.14'
            supervisor.event('connected')
            target = Path(config['config_dir']) / 'vpn-status.json'
            value = json.loads(target.read_text())
            self.assertEqual(set(value), {
                'provider', 'state', 'protocol', 'kill_switch', 'time', 'server_hostname',
                'server_endpoint', 'server_port', 'tunnel_ipv4'})
            self.assertEqual(value['state'], 'connected')
            self.assertEqual(value['protocol'], 'udp')
            self.assertEqual(value['server_hostname'], 'hu-budapest-1.provider.example')
            self.assertEqual(value['server_endpoint'], '203.0.113.10')
            self.assertEqual(value['server_port'], 15021)
            self.assertEqual(value['tunnel_ipv4'], '10.8.0.14')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            before = target.stat().st_mtime_ns
            supervisor.event('connected')
            self.assertEqual(target.stat().st_mtime_ns, before)
            supervisor.event('reconnecting')
            self.assertEqual(json.loads(target.read_text())['state'], 'reconnecting')


if __name__ == '__main__':
    unittest.main()
