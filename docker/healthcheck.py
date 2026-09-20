import json
from pathlib import Path
import ssl
import sys
import urllib.request

try:
    if Path('/run/container-vpn-enabled').read_text() == 'true':
        if json.loads(Path('/config/vpn-status.json').read_text()).get('state') != 'connected':
            sys.exit(1)
    context = ssl.create_default_context(cafile='/config/tls/server.crt')
    context.check_hostname = False  # The installed certificate may use a LAN DNS name.
    # OPTIONS reaches the real daemon through TLS/relay without fake login records.
    request = urllib.request.Request('https://127.0.0.1:9091/transmission/rpc', method='OPTIONS')
    with urllib.request.urlopen(request, context=context, timeout=3) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
