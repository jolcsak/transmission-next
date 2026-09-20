"""Typed environment overlay. Never modifies or persists the source JSON."""
import copy
import os

FIELDS = {
    'TRANSMISSION_TELEMETRY_INTERVAL_SECONDS': ('interval_seconds', int),
    'TRANSMISSION_TELEMETRY_HEARTBEAT_SECONDS': ('heartbeat_seconds', int),
    'TRANSMISSION_TELEMETRY_INCLUDE_HISTORY': ('include_history', bool),
}
for section, prefix, strings, integers, booleans in (
    ('rpc', 'TRANSMISSION_TELEMETRY_RPC_', 'url username password password_file', '', ''),
    ('mqtt', 'TRANSMISSION_MQTT_', 'host username password password_file client_id topic_prefix ca_file cert_file key_file', 'port', 'enabled tls'),
    ('influxdb', 'TRANSMISSION_INFLUXDB_', 'url org bucket token token_file instance ca_file', 'flush_seconds buffer_bytes', 'enabled include_directories'),
):
    for names, kind in ((strings, str), (integers, int), (booleans, bool)):
        for name in names.split():
            FIELDS[prefix + name.upper()] = (section + '.' + name, kind)


def overrides(environ=None):
    env = os.environ if environ is None else environ
    return sorted(name for name in FIELDS if name in env)


def resolve(config, environ=None):
    env = os.environ if environ is None else environ
    result = copy.deepcopy(config)
    if not isinstance(result, dict):
        raise ValueError('Configuration must be an object')
    for name in overrides(env):
        path, kind = FIELDS[name]
        raw = env[name]
        try:
            if kind is bool:
                normalized = raw.strip().lower()
                if normalized not in ('true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
                    raise ValueError()
                value = normalized in ('true', '1', 'yes', 'on')
            else:
                value = kind(raw)
        except (ValueError, TypeError):
            # Values may be credentials. Only name the variable.
            raise ValueError('Invalid environment variable: ' + name) from None
        parts = path.split('.')
        target = result
        if len(parts) == 2:
            target = result.setdefault(parts[0], {})
            if not isinstance(target, dict):
                raise ValueError('Invalid configuration section')
        key = parts[-1]
        # File overrides must also replace an inline credential inherited from JSON.
        if key in ('password', 'token'):
            target.pop(key + '_file', None)
        elif key in ('password_file', 'token_file'):
            target.pop(key.removesuffix('_file'), None)
        target[key] = value
    for section, prefix, secret in (('rpc', 'TRANSMISSION_TELEMETRY_RPC_', 'PASSWORD'),
                                    ('mqtt', 'TRANSMISSION_MQTT_', 'PASSWORD'),
                                    ('influxdb', 'TRANSMISSION_INFLUXDB_', 'TOKEN')):
        if prefix + secret in env and prefix + secret + '_FILE' in env:
            raise ValueError('Set only one of ' + prefix + secret + ' and ' + prefix + secret + '_FILE')
    return result
