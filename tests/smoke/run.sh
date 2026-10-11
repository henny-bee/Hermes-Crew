#!/usr/bin/env bash
# Real-Hermes smoke test: does the hermes-crew plugin still work with the installed Hermes?
#
#   bash tests/smoke/run.sh
#
# What it does (everything in throwaway dirs - the real ~/.hermes, ~/.local/bin and ~/.bashrc are never touched):
#   1. install.sh into a temp HOME / HERMES_HOME (copies and enables the plugin),
#   2. start ONE interactive Hermes session in its own tmux server with HERMES_TEAM_DIR / HERMES_TEAM_ROLE set,
#   3. assert .team/agents/<Role>.json reaches state "idle",
#   4. `hermes-crew send` one mail and assert a `msg.delivered` event appears in .team/events.jsonl.
# No model is needed or called: the provider points at a closed local port. The mail is delivered with
# ctx.inject_message; the model call that follows fails harmlessly and is not part of the assertions.
#
# Env: HERMES_SMOKE_REQUIRE=1  fail (exit 1) instead of skipping (exit 0) when hermes/tmux/python3 is missing
#      HERMES_SMOKE_ROLE       role name for the test agent (default Smoke)
#      HERMES_SMOKE_IDLE_S     seconds to wait for "idle" (default 120)
#      HERMES_SMOKE_MAIL_S     seconds to wait for msg.delivered (default 90)
#      HERMES_SMOKE_KEEP=1     keep the temp dir (printed at the end) for debugging
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ROLE=${HERMES_SMOKE_ROLE:-Smoke}
IDLE_S=${HERMES_SMOKE_IDLE_S:-120}
MAIL_S=${HERMES_SMOKE_MAIL_S:-90}

say() { printf '==> %s\n' "$*"; }
skip() {
  if [ "${HERMES_SMOKE_REQUIRE:-0}" = 1 ]; then echo "SMOKE FAIL: $*" >&2; exit 1; fi
  echo "SMOKE SKIP: $* (set HERMES_SMOKE_REQUIRE=1 to make this a failure)"; exit 0
}

HERMES_BIN=$(command -v hermes) || skip "hermes is not installed"
for c in tmux python3; do command -v "$c" >/dev/null || skip "$c is not installed"; done
HERMES_VERSION=$("$HERMES_BIN" --version 2>/dev/null | head -n 1 || true)

WORK=$(mktemp -d "${TMPDIR:-/tmp}/hermes-smoke.XXXXXX")
# tmux sockets live in a short path (unix socket paths are limited to ~100 chars)
TMUX_DIR=$(mktemp -d /tmp/hsm.XXXXXX)
cleanup() {
  local pids pid n
  pids=$(TMUX_TMPDIR=$TMUX_DIR tmux list-panes -a -F '#{pane_pid}' 2>/dev/null || true)
  TMUX_TMPDIR=$TMUX_DIR tmux kill-server 2>/dev/null || true
  if [ "${HERMES_SMOKE_KEEP:-0}" = 1 ]; then echo "kept: $WORK $TMUX_DIR"; return; fi
  # Hermes recreates files in its home while it shuts down: wait for the pane processes to exit first...
  for pid in $pids; do
    n=0
    while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 20 ]; do sleep 0.5; n=$((n + 1)); done
  done
  # ...then remove, and look again a moment later in case something wrote after the removal.
  for _ in 1 2 3 4 5 6; do
    rm -rf "$WORK" "$TMUX_DIR" 2>/dev/null
    sleep 1
    [ -e "$WORK" ] || return 0
  done
  echo "note: could not fully remove $WORK" >&2
}
trap cleanup EXIT

export HOME=$WORK/home HERMES_HOME=$WORK/hermes-home TMUX_TMPDIR=$TMUX_DIR
TEAM_DIR=$WORK/project
mkdir -p "$HOME/.local/bin" "$HERMES_HOME" "$TEAM_DIR"
HERMES_DIR=$(dirname "$HERMES_BIN")
export PATH=$HOME/.local/bin:$HERMES_DIR:$PATH
unset TMUX TMUX_PANE HERMES_TEAM_DIR HERMES_TEAM_ROLE

# No real credentials, no network: a dummy OpenAI-compatible endpoint on a closed port counts as "configured".
export OPENAI_API_KEY=smoke-dummy OPENAI_BASE_URL=http://127.0.0.1:9/v1
# Answer the first-run "share metrics?" question up front (Hermes asks it before an interactive chat).
cat >"$HERMES_HOME/config.yaml" <<'YAML'
telemetry:
  shared_metrics:
    enabled: false
    send: false
    offer_version: 2
YAML

fail() {
  echo "SMOKE FAIL: $*" >&2
  echo "--- hermes: ${HERMES_VERSION:-unknown}" >&2
  echo "--- pane:" >&2
  tmux capture-pane -p -t smoke 2>/dev/null | grep -v '^$' | tail -n 25 >&2 || true
  echo "--- agent state:" >&2
  cat "$TEAM_DIR/.team/agents/$ROLE.json" 2>/dev/null >&2 || echo "(none)" >&2
  echo "--- events:" >&2
  tail -n 10 "$TEAM_DIR/.team/events.jsonl" 2>/dev/null >&2 || echo "(none)" >&2
  exit 1
}

say "Hermes: ${HERMES_VERSION:-unknown}"
say "Installing the plugin into a temporary HOME / HERMES_HOME"
bash "$ROOT/install.sh" >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log" >&2; fail "install.sh failed"; }
[ -f "$HERMES_HOME/plugins/hermes-crew/plugin.yaml" ] || fail "plugin was not installed into $HERMES_HOME/plugins"
"$HERMES_BIN" plugins list --plain </dev/null 2>/dev/null | grep -i 'hermes-crew' | grep -qi 'enabled' \
  || echo "note: could not confirm 'enabled' via 'hermes plugins list --plain' (continuing)"

agent_state() {
  python3 - "$TEAM_DIR/.team/agents/$ROLE.json" <<'PY' 2>/dev/null
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("state", ""))
except Exception:
    print("")
PY
}
wait_until() { # seconds cmd...
  local n=$1; shift
  while [ "$n" -gt 0 ]; do "$@" && return 0; sleep 1; n=$((n - 1)); done
  return 1
}
is_idle() { [ "$(agent_state)" = idle ]; }
has_delivered() { grep -q '"msg.delivered"' "$TEAM_DIR/.team/events.jsonl" 2>/dev/null; }

say "Starting one headless Hermes session as role $ROLE (own tmux server)"
tmux new-session -d -s smoke -x 200 -y 50 -c "$TEAM_DIR" \
  "env HERMES_TEAM_DIR='$TEAM_DIR' HERMES_TEAM_ROLE='$ROLE' '$HERMES_BIN' chat --cli; sleep 3600" \
  || fail "could not start tmux"

wait_until "$IDLE_S" is_idle || fail "agents/$ROLE.json did not reach 'idle' within ${IDLE_S}s (state: '$(agent_state)')"
say "agents/$ROLE.json is idle"

# Send as lead into the agent's mailbox.
HERMES_TEAM_DIR=$TEAM_DIR HERMES_TEAM_ROLE=lead "$HOME/.local/bin/hermes-crew" send "$ROLE" "smoke test ping" \
  >"$WORK/send.log" 2>&1 || { cat "$WORK/send.log" >&2; fail "hermes-crew send failed"; }
wait_until "$MAIL_S" has_delivered || fail "no msg.delivered event within ${MAIL_S}s"
say "msg.delivered seen"
echo "SMOKE PASS (${HERMES_VERSION:-hermes unknown})"
