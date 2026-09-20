"""Typed VPN environment overlay, shared by the CLI and unprivileged admin."""
import copy
import os

FIELDS = {name.upper(): (name, str) for name in (
    'provider openvpn_config openvpn_profile credentials_file username password '
    'run_user daemon config_dir download_dir subnet').split()}
FIELDS.update({name.upper(): (name, int) for name in ('rpc_port', 'start_timeout', 'vpn_log_retention', 'vpn_log_flush_seconds')})
FIELDS['DNS'] = ('dns', list)
PREFIX = 'TRANSMISSION_VPN_'


def overrides(environ=None):
    env = os.environ if environ is None else environ
    return sorted(PREFIX + name for name in FIELDS if PREFIX + name in env)


def resolve(config, environ=None):
    env = os.environ if environ is None else environ
    if not isinstance(config, dict):
        raise ValueError('VPN configuration must be an object')
    for group in (('OPENVPN_CONFIG', 'OPENVPN_PROFILE'),
                  ('CREDENTIALS_FILE', 'USERNAME'), ('CREDENTIALS_FILE', 'PASSWORD')):
        if all(PREFIX + key in env for key in group):
            raise ValueError('Conflicting VPN environment variables: ' + ', '.join(PREFIX + key for key in group))
    result = copy.deepcopy(config)
    for variable in overrides(env):
        field, kind = FIELDS[variable.removeprefix(PREFIX)]
        try:
            raw = env[variable]
            value = [part.strip() for part in raw.split(',')] if kind is list else kind(raw)
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid VPN environment variable: ' + variable) from None
        if field == 'credentials_file':
            result.pop('username', None)
            result.pop('password', None)
        elif field in ('username', 'password'):
            result.pop('credentials_file', None)
        elif field == 'openvpn_config':
            result.pop('openvpn_profile', None)
        elif field == 'openvpn_profile':
            result.pop('openvpn_config', None)
        result[field] = value
    return result
