"""PureVPN manual OpenVPN profile adapter. No account passwords or API scraping."""
from collections import namedtuple
from pathlib import Path
import ipaddress
import re
import shlex
import socket


def quote(value):
    if any(c in str(value) for c in '\r\n\x00'):
        raise ValueError("Invalid multiline OpenVPN option")
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


class Profile(namedtuple('ProfileFields', 'hostname port protocol crypto')):
    __slots__ = ()

    def resolve(self):
        addresses = socket.getaddrinfo(self.hostname, self.port, socket.AF_INET,
                                      socket.SOCK_DGRAM if self.protocol == 'udp' else socket.SOCK_STREAM)
        return str(ipaddress.IPv4Address(addresses[0][4][0]))

    def render(self, endpoint, credentials, management):
        endpoint = str(ipaddress.IPv4Address(endpoint))
        # Do not disable opportunistic DCO for profiles without compression.
        legacy_compression = any(line.split()[0] in ('compress', 'comp-lzo')
                                 for line in self.crypto.splitlines() if line.strip())
        return self.crypto + '\n' + '\n'.join([
            'client', 'dev tun0', 'dev-type tun', 'nobind',
            'proto ' + ('udp4' if self.protocol == 'udp' else 'tcp4-client'),
            f'remote {endpoint} {self.port}',
            'remote-cert-tls server', 'tls-version-min 1.2',
            'auth-user-pass ' + quote(credentials), 'auth-nocache', 'auth-retry none',
            'management ' + quote(management) + ' unix',
            'script-security 1', 'route-nopull', 'redirect-gateway def1',
            'pull-filter ignore "route-ipv6"', 'pull-filter ignore "ifconfig-ipv6"',
            'pull-filter ignore "comp-lzo"', 'pull-filter ignore "compress"',
            'allow-compression ' + ('asym' if legacy_compression else 'no'),
            *(['fast-io'] if self.protocol == 'udp' else []),
            'persist-key', 'persist-tun',
            'ping 20', 'ping-restart 60', 'connect-timeout 15',
            'connect-retry 5 60', 'resolv-retry 0', 'verb 3', 'mute 10', ''
        ])


class PureVPN:
    name = 'PureVPN'
    protocol = 'OpenVPN'
    credentials_help = 'Use the VPN credentials from the PureVPN member area, not the website login.'
    # Only declarative cryptographic options may survive profile import.
    CRYPTO = {'cipher', 'auth', 'data-ciphers', 'data-ciphers-fallback',
              'tls-cipher', 'tls-ciphersuites', 'verify-x509-name', 'key-direction'}
    INLINE = {'ca', 'cert', 'key', 'tls-auth', 'tls-crypt'}
    MANAGED = {'client', 'dev', 'dev-type', 'nobind', 'persist-key', 'persist-tun',
               'resolv-retry', 'verb', 'mute', 'auth-user-pass', 'auth-nocache',
               'remote-random', 'route-delay', 'redirect-gateway', 'route-nopull',
               'ping', 'ping-restart', 'connect-retry', 'connect-timeout',
               'fast-io', 'sndbuf', 'rcvbuf', 'explicit-exit-notify', 'reneg-sec'}

    @classmethod
    def load(cls, path, *, allow_external=True):
        path = Path(path).resolve()
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('OpenVPN profile is too large')
        lines = iter(path.read_text(encoding='utf-8-sig').splitlines())
        crypto, remotes, present, options = [], [], set(), {}
        proto = 'udp'
        for line in lines:
            line = line.strip()
            if not line or line.startswith(('#', ';')):
                continue
            if line.startswith('<'):
                tag = line[1:-1]
                if line != f'<{tag}>' or tag not in cls.INLINE or tag in present:
                    raise ValueError('Unsupported or duplicate inline OpenVPN block')
                content = []
                for line in lines:
                    if line.strip() == f'</{tag}>':
                        break
                    if '<' in line or '>' in line:
                        raise ValueError('Invalid nested inline block')
                    content.append(line)
                else:
                    raise ValueError('Unterminated inline OpenVPN block')
                crypto.append(f'<{tag}>\n' + '\n'.join(content) + f'\n</{tag}>')
                present.add(tag)
                continue
            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = '#;'
            tokens = list(lexer)
            if not tokens:
                continue
            option, *args = tokens
            option = option.removeprefix('--')
            if option == 'remote':
                if not 1 <= len(args) <= 3 or not re.fullmatch(r'[A-Za-z0-9._-]+', args[0]):
                    raise ValueError('Invalid remote endpoint')
                remotes.append(args)
            elif option == 'proto':
                if len(args) != 1:
                    raise ValueError('Invalid OpenVPN protocol')
                proto = args[0]
            elif option in cls.INLINE:
                if not allow_external:
                    raise ValueError('Connection tests require inline certificates and keys')
                if not 1 <= len(args) <= (2 if option == 'tls-auth' else 1) or option in present:
                    raise ValueError('Invalid certificate/key directive')
                source = (path.parent / args[0]).resolve()
                if source.stat().st_size > 1024 * 1024:
                    raise ValueError('Certificate/key file is too large')
                content = source.read_text()
                if '<' in content or '>' in content:
                    raise ValueError('Invalid certificate/key data')
                crypto.append(f'<{option}>\n{content.rstrip()}\n</{option}>')
                present.add(option)
                if len(args) == 2:
                    if args[1] not in ('0', '1'):
                        raise ValueError('Invalid key direction')
                    crypto.append('key-direction ' + args[1])
            elif option in cls.CRYPTO:
                if not args or len(args) > 2:
                    raise ValueError('Invalid cryptographic directive')
                if option in ('cipher', 'data-ciphers', 'data-ciphers-fallback') and any('none' in arg.lower().split(':') for arg in args):
                    raise ValueError('Unencrypted data ciphers are forbidden')
                crypto.append(option + ' ' + ' '.join(map(quote, args)))
                options[option] = args
            elif option == 'remote-cert-tls':
                if args != ['server']:
                    raise ValueError('Server certificate verification is mandatory')
            elif option == 'tls-version-min':
                if args not in (['1.2'], ['1.3']):
                    raise ValueError('TLS 1.2 or newer is required')
                if args == ['1.3']:
                    raise ValueError('TLS 1.3-only profiles are not supported by this adapter yet')
            elif option == 'comp-lzo':
                # Receive legacy compressed traffic, never compress outgoing data.
                if args not in ([], ['yes'], ['no'], ['adaptive']):
                    raise ValueError('Invalid compression option')
                crypto.append('comp-lzo no')
            elif option == 'compress':
                if args not in ([], ['stub'], ['stub-v2']):
                    raise ValueError('Use a profile without outgoing compression')
                # PureVPN currently negotiates the non-compressing stub-v2
                # framing even when older profile exports say just "stub".
                # Pin it here so the local data channel matches the server
                # before its filtered PUSH options are processed.
                crypto.append('compress stub-v2')
            elif option == 'script-security':
                if args not in (['0'], ['1'], ['2']):
                    raise ValueError('Unsupported script security setting')
                # The generated profile always uses level 1; hooks remain forbidden.
            elif option == 'route-method':
                if args not in (['exe'], ['ipapi'], ['adaptive']):
                    raise ValueError('Unsupported route method')
            elif option == 'route':
                if args != ['0.0.0.0', '0.0.0.0']:
                    raise ValueError('Custom profile routes are forbidden')
                # PureVPN's default route is replaced by managed redirect-gateway.
            elif option in cls.MANAGED:
                if option == 'dev' and args and not args[0].startswith('tun'):
                    raise ValueError('Only routed TUN profiles are supported')
            else:
                raise ValueError(f'Unsupported OpenVPN option: {option}; scripts/plugins/includes are forbidden')
        if not remotes or 'ca' not in present:
            raise ValueError('A remote endpoint and CA certificate are required')
        remote = remotes[0]
        proto = remote[2] if len(remote) == 3 else proto
        mapping = {'udp': 'udp', 'udp4': 'udp', 'tcp': 'tcp', 'tcp-client': 'tcp', 'tcp4-client': 'tcp'}
        if proto not in mapping:
            raise ValueError('Only IPv4 OpenVPN UDP or TCP client transport is supported')
        port = int(remote[1]) if len(remote) > 1 else 1194
        if not 1 <= port <= 65535:
            raise ValueError('Invalid endpoint port')
        if 'cipher' in options:
            legacy = options['cipher'][0]
            if 'data-ciphers' not in options:
                choices = list(dict.fromkeys(['AES-256-GCM', 'AES-128-GCM', 'CHACHA20-POLY1305', legacy]))
                crypto.append('data-ciphers ' + quote(':'.join(choices)))
            if 'data-ciphers-fallback' not in options:
                crypto.append('data-ciphers-fallback ' + quote(legacy))
        return Profile(remote[0], port, mapping[proto], '\n'.join(crypto))
