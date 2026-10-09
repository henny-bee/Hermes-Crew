"""Helpers for plugin adapter tests: load the plugin like Hermes does, and a fake PluginContext."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugin" / "hermes-crew"
MODULE_NAME = "hermes_crew_plugin_under_test"


def load_plugin():
    """Import plugin/hermes-crew/__init__.py as a package, the way Hermes' loader does
    (spec_from_file_location + submodule_search_locations → relative imports work)."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    """Records register_hook calls; fire() calls callbacks with kwargs like Hermes does."""

    def __init__(self, accept: bool = True):
        self.hooks: dict[str, list] = {}
        self.injected: list[str] = []
        self.accept = accept
        self.raise_on_inject = False

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)

    def inject_message(self, content, role="user", *, session_key=None, origin=None):
        if self.raise_on_inject:
            raise RuntimeError("inject exploded")
        if self.accept:
            self.injected.append(content)
        return self.accept

    def fire(self, name, **kw):
        kw.setdefault("telemetry_schema_version", "hermes.observer.v1")
        results = [cb(**kw) for cb in self.hooks.get(name, [])]
        return results[0] if len(results) == 1 else results
