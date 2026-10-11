# Hermes API contract

What the `hermes-crew` plugin (`plugin/hermes-crew/__init__.py`) relies on from Hermes Agent.
When Hermes changes one of these, the plugin degrades or breaks. Check this page first after a Hermes upgrade.

**Verified against:** Hermes Agent **v0.21.5** (git `85db7c3a`, 2026-10-06), source in
`~/.hermes/hermes-agent`. Paths below are relative to that checkout. Line numbers are from that commit and will drift.

## Plugin loading

| What | Where | Contract |
|---|---|---|
| Discovery | `hermes_cli/plugins_discovery.py` (`collect_directory_manifests`) | Reads `~/.hermes/plugins/<name>/plugin.yaml`. A `kind: standalone` user plugin loads only if listed in `plugins.enabled` (`gate_manifest`). |
| Manifest | `hermes_cli/plugins_manifest.py` (`parse_manifest_file`) | `name`, `version`, `kind` are used. `provides_hooks` is metadata only, checked by plugin dev/validate tooling and not at runtime. `platforms` is accepted but **not enforced**: it documents support and does not gate loading. |
| Entry point | `hermes_cli/plugins_loader.py` (`_load_plugin_scoped`) | Imports `__init__.py` as `hermes_plugins.<slug>` and calls `register(ctx)` on a worker thread with a deadline (`plugins.load_timeout_seconds`, default 10 s). The return value is ignored. If `register` raises or times out, its registrations are removed. |

## `ctx` methods called

| Method | Where | Signature / semantics the plugin relies on |
|---|---|---|
| `ctx.register_hook(name, callback)` | `hermes_cli/plugins.py:933` (`PluginContext`) | Returns a registration. An unknown hook name logs a WARNING (listing `VALID_HOOKS`, `hermes_cli/plugins.py:107`) but is still stored, and **never raises**. It is a no-op after an abandoned (timed-out) load. Callbacks get the full kwargs payload when they accept `**kw`. |
| `ctx.inject_message(content, role="user", *, session_key=None, origin=None) -> bool` | `hermes_cli/plugins.py:603`, implemented in `hermes_cli/plugins_injection.py` | **Classic CLI** (`display.interface: cli`, the default): if a turn is running, the text goes to the interrupt queue and **interrupts the busy turn**; otherwise it becomes the next prompt. Always returns `True`. **Elsewhere** (Ink TUI, desktop, gateway) a bare `inject_message(text)` returns `False`, because a `session_key` or `origin` and `plugins.entries.<id>.allow_gateway_injection: true` are required. `True` means "a host accepted it", not that a turn ran. The plugin therefore injects only while the agent is idle, and un-claims mail when it gets `False`. |

## Hooks used (`CrewPlugin.HOOKS`)

All of these go through `hermes_cli/lifecycle.py` `invoke_hook` → `hermes_cli/plugins_dispatch.py` `invoke_hook`.
Every payload also carries `telemetry_schema_version`. "Bounded" hooks run under `plugins.hook_callback_timeout`
(default 30 s). A timed-out callback is abandoned and suppressed for 60 s.

| Hook | Fired at | Kwargs the plugin reads | Return value expected | Bound |
|---|---|---|---|---|
| `on_session_start` | `agent/conversation_loop.py:864`, when the system prompt is built (not on a resume with a stored prompt) | `model`, `session_id` | none (ignored) | 30 s, fail-open |
| `pre_llm_call` | `agent/turn_context.py:793`, once per user turn | `turn_id`, `model` | **must be `None`**: a `str` or `{"context": str}` is appended to the user message | 30 s, fail-open |
| `on_session_end` | `agent/turn_finalizer.py:788`, end of every turn. Also `hermes_cli/cli_shutdown.py:187` on CLI exit and `tui_gateway/session_lifecycle.py:388` on TUI close | `failed`, `interrupted`, `turn_exit_reason`, `reason`, `turn_id` (`reason` only on the exit path, `turn_id` absent on the TUI path) | none | 30 s, fail-open |
| `on_session_finalize` | `hermes_cli/lifecycle.py` `finalize_session`: CLI `/new` (`hermes_cli/cli_session_mixin.py:444`), CLI exit, `hermes_cli/oneshot.py`, tui_gateway, gateway shutdown | `reason`: `"session_boundary"` (CLI `/new`, process lives on), `"new_session"` (gateway `/new`), `"shutdown"` (exit), missing (tui_gateway) | none | unbounded |
| `on_session_reset` | CLI `/new` (`hermes_cli/cli_session_mixin.py:446`), tui_gateway (`tui_gateway/server.py:1063`), gateway `/new` (`gateway/slash_commands_session.py:213`) | none | none | unbounded |
| `on_human_input_request` | `tools/human_input_hooks.py:68` | `kind` (`approval`, `clarify`, `sudo`) | none | unbounded |
| `on_human_input_resolved` | `tools/human_input_hooks.py:72` (in `finally`, always fires) | none | none | unbounded |
| `post_api_request` | `agent/turn_response_intake.py:68` | `usage` (dict or `None`: `input_tokens`, `output_tokens`, `prompt_tokens`, `cache_read_tokens`, `cache_write_tokens`, …), `model` | none | 30 s, fail-open |
| `api_request_error` | `agent/api_request_hooks.py:176` (classified errors incl. retryable ones) | `error` (dict `{"type","message"}`), `reason`, `status_code` | none | 30 s, fail-open |
| `pre_tool_call` | `hermes_cli/plugins.py:1987`, reached from `model_tools.py:783` | `tool_name`, `args` (always a dict). Tool names `terminal`, `write_file`, `patch` | `None` = allow; `{"action": "block", "message": "<non-empty>"}` = block (message becomes the tool result). A block without a message is ignored. **A raising callback becomes a block**, so the guard must never raise. | 30 s, **fail-closed** (timeout = block) |
| `post_tool_call` | `model_tools.py:697` | `tool_name`, `result` (JSON string; contains `"cwd"` only when the command changed directory) | none | 30 s, fail-open |

## Known gaps (Hermes behaviour the plugin does not fully cover)

These were found while writing this contract. They are recorded here rather than fixed, because changing behaviour
is out of scope for T21.

1. **Mail delivery is classic-CLI only.** Under the Ink TUI, desktop or gateway, `inject_message(text)` returns `False`, so
   mail stays pending. Teammate panes use the classic CLI (`hermes-team-spawn`), so the supported setup works.
2. **Gateway `/new` ends the plugin.** The gateway's `/new` finalizes with `reason="new_session"`, but the plugin only
   exempts `"session_boundary"`, so it would mark the agent `exited`.
3. **Spurious `turn.interrupted` events.** tui_gateway fires `on_session_end(interrupted=True)` on every session close
   with no `turn_id`, so an idle agent can log `turn.interrupted`.
4. **Resumed sessions skip `on_session_start`.** The plugin's 30 s startup grace (`mail_tick`) moves such an agent to
   `idle`.
5. **End-of-turn inject race (CLI).** `on_session_end` fires before the CLI clears `_agent_running`. An inject in that
   short window is treated as an interrupt and re-queued as the next prompt, so it is delivered but the hand-off is not clean.
6. **Token counts include cache.** `usage["prompt_tokens"]` includes cache read/write tokens. The plugin reads it before
   `input_tokens`, so "tokens in" counts cached tokens.

T30 (`compat.py`) records which Hermes versions are known OK. T31 (degraded mode) reports missing hooks or a missing
`inject_message` at registration time.
