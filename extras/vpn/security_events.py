"""Bounded local nginx security-event collector; no request contents or disk spool."""
import ipaddress
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct
import time


class SecurityEvents:
    def __init__(self, path='/run/transmission-rpc-security/events.sock'):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o755, exist_ok=True)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Security event directory must be root-owned and private to writers')
        # The supervisor runs with umask 0077; nginx must still be able to
        # traverse this directory. Only the socket's group may send events.
        self.path.parent.chmod(0o755)
        user = pwd.getpwnam('www-data')
        self.uid = user.pw_uid
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
                raise ValueError('Unsafe security event socket')
            self.path.unlink()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
        self.sock.bind(str(self.path))
        os.chown(self.path, 0, user.pw_gid)
        self.path.chmod(0o620)
        self.sock.setblocking(False)
        self.pending = {}
        self.last = {}

    @staticmethod
    def parse(data):
        match = re.search(rb'\bcode=(400|403|413|414|429|494) peer=([0-9a-fA-F:.]+)$', data.rstrip())
        if not match:
            return None
        try:
            return int(match[1]), str(ipaddress.ip_address(match[2].decode('ascii')))
        except ValueError:
            return None

    def poll(self, emit, force=False):
        now = time.monotonic()
        for _ in range(256):
            try:
                data, ancillary, flags, _ = self.sock.recvmsg(512, socket.CMSG_SPACE(12))
            except BlockingIOError:
                break
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                continue
            trusted = any(level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and
                          len(value) == 12 and struct.unpack('3i', value)[1] == self.uid
                          for level, kind, value in ancillary)
            event = self.parse(data) if trusted else None
            if event:
                code, peer = event
                count, _ = self.pending.get(code, (0, peer))
                self.pending[code] = (min(count + 1, 2**63-1), peer)
        for code, (count, peer) in list(self.pending.items()):
            if force or code not in self.last or now - self.last[code] >= 60:
                emit('Security', f'event=http_request_rejected status={code} count={count} last_proxy_peer={peer}', 3)
                self.last[code] = now
                del self.pending[code]

    def close(self):
        self.sock.close()
        self.path.unlink(missing_ok=True)
