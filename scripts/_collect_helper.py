"""Internal helper to safely import 07_collect functions."""
import importlib.util
import os

def _load_helper():
    here = os.path.dirname(__file__)
    mod_path = os.path.join(here, "07_collect.py")
    spec = importlib.util.spec_from_file_location("_07_collect", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_helper()
resolve_picks = _mod.resolve_picks
build_collect_plan = _mod.build_collect_plan
