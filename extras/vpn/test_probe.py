import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from probe_service import decode_request, receive


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.request = dict(profile='remote vpn.test 1194\n<ca>\nfixture\n</ca>\n',
                            secret='fixture\npassword\n', timeout=5)

    def tearDown(self):
        self.temp.cleanup()

    def test_inline_profile(self):
        profile, secret = decode_request(self.request, self.folder)
        self.assertEqual(profile.hostname, 'vpn.test')
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_root_boundary_rejects_external_files(self):
        for option in ('ca /etc/shadow', 'key /etc/shadow', 'config /etc/shadow', 'up /bin/true', 'plugin /tmp/x'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                decode_request(dict(self.request, profile=self.request['profile']+option+'\n'), self.folder)

    def test_request_allowlist_and_bounds(self):
        for change in ({'daemon': '/bin/sh'}, {'timeout': 61}, {'timeout': True},
                       {'secret': 'one-line'}, {'secret': 'x'*4097}, {'profile': 'x'*60001}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                decode_request(dict(self.request, **change), self.folder)

    def test_bounded_socket_receive(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(json.dumps(self.request).encode())
            left.shutdown(socket.SHUT_WR)
            self.assertEqual(receive(right), self.request)


if __name__ == '__main__':
    unittest.main()
