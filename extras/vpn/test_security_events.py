import unittest
from unittest import mock
from security_events import SecurityEvents
import socket
import struct
import os
import tempfile
import subprocess
import sys
from pathlib import Path


class SecurityEventsTest(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, 'real sender test needs UID transition')
    def test_real_worker_can_send_under_private_umask(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = os.umask(0o077)
            try:
                obj = SecurityEvents(str(Path(folder) / 'events.sock'))
            finally:
                os.umask(previous)
            try:
                code = "import os,pwd,socket,sys; u=pwd.getpwnam('www-data'); os.setgroups([]); os.setgid(u.pw_gid); os.setuid(u.pw_uid); s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM); s.sendto(b'code=413 peer=127.0.0.1',sys.argv[1])"
                subprocess.run([sys.executable, '-c', code, str(obj.path)], check=True)
                emit = mock.Mock()
                obj.poll(emit)
                emit.assert_called_once()
                self.assertIn('status=413', emit.call_args.args[1])
            finally:
                obj.close()

    def collector(self, uid=33):
        obj = SecurityEvents.__new__(SecurityEvents)
        obj.uid, obj.pending, obj.last = uid, {}, {}
        obj.sock = mock.Mock()
        return obj

    def packet(self, message, uid=33, flags=0):
        return message, [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack('3i', 100, uid, 33))], flags, None

    def test_parses_only_allowed_numeric_metadata(self):
        self.assertEqual(SecurityEvents.parse(b'<190>nginx: code=413 peer=127.0.0.1'), (413, '127.0.0.1'))
        for text in (b'code=200 peer=127.0.0.1', b'code=413 peer=invalid', b'code=413 peer=127.0.0.1 password=secret'):
            self.assertIsNone(SecurityEvents.parse(text))

    def test_sender_credentials_and_truncation(self):
        obj = self.collector()
        obj.sock.recvmsg.side_effect = [self.packet(b'code=413 peer=127.0.0.1', uid=99),
                                        self.packet(b'code=413 peer=127.0.0.1', flags=socket.MSG_TRUNC), BlockingIOError()]
        emit = mock.Mock()
        obj.poll(emit)
        emit.assert_not_called()

    def test_aggregation_flushes_even_after_flood_stops(self):
        obj = self.collector()
        emit = mock.Mock()
        with mock.patch('security_events.time.monotonic', return_value=100):
            obj.sock.recvmsg.side_effect = [self.packet(b'code=413 peer=127.0.0.1'), BlockingIOError()]
            obj.poll(emit)
            self.assertEqual(emit.call_count, 1)
            obj.sock.recvmsg.side_effect = [self.packet(b'code=413 peer=127.0.0.1') for _ in range(256)]
            obj.poll(emit)
            self.assertEqual(emit.call_count, 1)
            self.assertEqual(obj.pending[413][0], 256)
        with mock.patch('security_events.time.monotonic', return_value=161):
            obj.sock.recvmsg.side_effect = BlockingIOError()
            obj.poll(emit)
        self.assertIn('count=256', emit.call_args.args[1])
        self.assertFalse(obj.pending)


if __name__ == '__main__': unittest.main()
