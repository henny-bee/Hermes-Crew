#!/usr/bin/env python3
"""Write a minimal, secret-free config.yaml for a throwaway HERMES_HOME: same model/provider as the real one,
the API key only by ENV VAR NAME (key_env), no MCP servers, no memory, no update checks.

usage: mkconfig.py <real-config.yaml> <out-config.yaml>
"""

import sys
from pathlib import Path

import yaml

src, dst = sys.argv[1:3]
real = yaml.safe_load(Path(src).read_text()) or {}
model = dict(real.get("model") or {})
model.pop("api_key", None)
model["key_env"] = "HERMES_CUSTOM_LOCALHOST_4000_API_KEY"  # bare `provider: custom` reads its key through model.key_env
prov = []
for p in real.get("custom_providers") or []:
    p = {k: v for k, v in p.items() if k not in ("api_key", "key")}
    prov.append(p)
cfg = {
    "_config_version": real.get("_config_version"),
    "model": model,
    "custom_providers": prov,
    "telemetry": {"shared_metrics": {"enabled": False, "send": False, "offer_version": 2}},
    "onboarding": real.get("onboarding") or {},
    "updates": {"check": False},
    "memory": {"memory_enabled": False, "user_profile_enabled": False},
    "agent": {"max_turns": 40, "reasoning_effort": "low"},
    "approvals": real.get("approvals") or {},
    "display": {"skin": "default"},
    "terminal": {"backend": "local", "cwd": ".", "timeout": 180},
}
cfg = {k: v for k, v in cfg.items() if v is not None}
Path(dst).write_text(yaml.safe_dump(cfg, sort_keys=False))
print("config written:", dst, "| model:", model.get("default"), "| provider:", model.get("provider"))
