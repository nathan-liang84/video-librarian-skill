"""Helper to expose resolve_picks for E2E without circular dependency or modifying tests."""
from scripts import _collect_helper as _ch

def resolve_picks(picks, manifest):
    return _ch.resolve_picks(picks, manifest)
