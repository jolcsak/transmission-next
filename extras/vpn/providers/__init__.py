"""Explicit provider registry: never load executable plugins from user config."""
from .purevpn import PureVPN

PROVIDERS = {"purevpn": PureVPN}
