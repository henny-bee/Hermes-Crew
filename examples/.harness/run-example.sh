#!/usr/bin/env bash
# T60: run ONE real Hermes team on a throwaway project and collect the evidence.
#   bash examples/.harness/run-example.sh <NN-name> [timebox-minutes]   (NN-name = directory under seed/)
#   bash run-example.sh probe                              (one tiny model call to check proxy + auth, then exit)
# Everything lives in a temp dir: HOME, HERMES_HOME, tmux server, project. The real ~/.hermes, ~/.local/bin,
# ~/.bashrc and this repo's .team/ are never touched. The API key is passed ONLY as an environment variable
# (read from the real ~/.hermes/.env at start, never written to any file, never printed).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
REAL_HOME=$HOME
KEYVAR=HERMES_CUSTOM_LOCALHOST_4000_API_KEY
NAME=${1:?usage: run-example.sh <NN-name>|probe [minutes]}
MAXMIN=${2:-20}

HERMES_BIN=$(command -v hermes) || { echo "hermes not found" >&2; exit 1; }
# The proxy accepts the key from the real .env (HERMES_CUSTOM_LOCALHOST_4000_API_KEY); the literal model.api_key in the
# real config.yaml is rejected by it. Hermes finds the key for `provider: custom` through model.key_env (mkconfig.py).
KEY=$(grep -m1 "^$KEYVAR=" "$REAL_HOME/.hermes/.env" | cut -d= -f2-)
[ -n "$KEY" ] || { echo "no $KEYVAR in the real .env" >&2; exit 1; }

WORK=$(mktemp -d /tmp/t60.XXXXXX)
export HOME=$WORK/home HERMES_HOME=$WORK/hh TMUX_TMPDIR=$WORK/tm LC_ALL=C.UTF-8
export "$KEYVAR=$KEY"
PROJ=$WORK/proj
mkdir -p "$HOME/.local/bin" "$HERMES_HOME" "$TMUX_TMPDIR" "$PROJ" "$WORK/out"
export PATH=$HOME/.local/bin:$(dirname "$HERMES_BIN"):/usr/local/bin:/usr/bin:/bin
unset TMUX TMUX_PANE HERMES_TEAM_DIR HERMES_TEAM_ROLE HERMES_CREW_SESSION HERMES_CREW_AUTO_OWN HERMES_CREW_LIB HERMES_TEAM_MODELS
say() { printf '==> %s\n' "$*"; }
say "work dir: $WORK"

python3 "$HERE/mkconfig.py" "$REAL_HOME/.hermes/config.yaml" "$HERMES_HOME/config.yaml" || exit 1

cleanup() {
  local pids pid n
  pids=$(tmux list-panes -a -F '#{pane_pid}' 2>/dev/null || true)
  tmux kill-server 2>/dev/null || true
  for pid in $pids; do n=0; while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 20 ]; do sleep 0.5; n=$((n + 1)); done; done
}
trap cleanup EXIT

if [ "$NAME" = probe ]; then
  say "probe: ONE tiny model call (max 1 turn)"
  "$HERMES_BIN" chat -q "Reply with exactly: OK" --max-turns 1 </dev/null 2>&1 | tail -n 15
  exit 0
fi

SEED=$HERE/seed/$NAME
[ -d "$SEED" ] || { echo "no seed $SEED" >&2; exit 1; }
say "installing the repo's plugin and scripts into the temp HOME/HERMES_HOME"
bash "$REPO/install.sh" >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log" >&2; exit 1; }
CREW=$HOME/.local/bin/hermes-crew

say "seeding project $NAME"
cp "$SEED"/* "$PROJ"/ && rm -f "$PROJ/PROMPT.md"
cp "$SEED/PROMPT.md" "$WORK/prompt.md"
printf '__pycache__/\n*.pyc\n' > "$PROJ/.gitignore"
git -C "$PROJ" init -q -b main
git -C "$PROJ" add -A
git -C "$PROJ" -c user.name=seed -c user.email=seed@example.invalid commit -q -m "seed: $NAME"

S=ex
{
  echo "example: $NAME"
  echo "started: $(date -Iseconds)"
  echo "hermes: $("$HERMES_BIN" --version 2>/dev/null | head -n 1)"
  echo "hermes-crew: $("$CREW" --version)"
  echo "repo head: $(git -C "$REPO" rev-parse --short HEAD) (+ uncommitted working tree)"
  echo "model: $(python3 -c "import yaml;print(yaml.safe_load(open('$HERMES_HOME/config.yaml'))['model'].get('default'))")"
  echo "config: max_turns 40, reasoning_effort low, memory off, no MCP servers; approvals mode default"
  echo "timebox: ${MAXMIN} min"
  echo "command: env -u HERMES_TEAM_DIR -u HERMES_TEAM_ROLE bash examples/.harness/run-example.sh $NAME $MAXMIN"
} > "$WORK/out/meta.txt"

say "starting the lead in a detached tmux session (own server)"
tmux new-session -d -s "$S" -x 220 -y 60 -c "$PROJ" \
  "env HERMES_TEAM_DIR=$PROJ HERMES_TEAM_ROLE=lead HERMES_CREW_SESSION=$S hermes-shared chat --cli -s hermes-tmux-team --query-file $WORK/prompt.md" \
  || { echo "tmux failed" >&2; exit 1; }
tmux set -t "$S" mouse on
tmux set -w -t "$S" pane-border-status top
tmux set -w -t "$S" pane-border-format ' #{?@role,#{@role},#{pane_index}} '
tmux set -w -t "$S" @team_dir "$PROJ"
tmux set -p -t "$S" @role lead

as_user() { HERMES_TEAM_DIR=$PROJ HERMES_TEAM_ROLE=user "$CREW" "$@"; }
LOG=$WORK/out/progress.log
deadline=$(( $(date +%s) + MAXMIN * 60 ))
done_at=0; last_ev=0; last_change=$(date +%s)
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 20
  ev=$(wc -c < "$PROJ/.team/events.jsonl" 2>/dev/null || echo 0)
  now=$(date +%s)
  [ "$ev" != "$last_ev" ] && { last_ev=$ev; last_change=$now; }
  {
    echo "--- $(date +%T) events=$ev quiet=$((now - last_change))s"
    as_user status 2>&1 | sed -n 1,14p
  } >> "$LOG"
  if as_user status --done-check >/dev/null 2>&1; then
    if [ "$done_at" = 0 ]; then done_at=$now; say "done-check passes; letting the lead report for 60 s"; fi
    [ $((now - done_at)) -ge 60 ] && break
  fi
  tmux has-session -t "$S" 2>/dev/null || { say "tmux session ended"; break; }
done
echo "ended: $(date -Iseconds) (done-check first passed: $([ "$done_at" = 0 ] && echo never || date -d "@$done_at" -Iseconds))" >> "$WORK/out/meta.txt"

say "collecting"
mkdir -p "$WORK/out/panes"
for p in $(tmux list-panes -s -t "$S" -F '#{pane_id}:#{@role}' 2>/dev/null); do
  tmux capture-pane -p -t "${p%%:*}" -S -3000 > "$WORK/out/panes/${p#*:}.txt" 2>/dev/null
done
as_user status --json > "$WORK/out/status.json" 2>&1
as_user status > "$WORK/out/status.txt" 2>&1
as_user status --done-check > "$WORK/out/done-check.txt" 2>&1; echo "done-check exit: $?" >> "$WORK/out/done-check.txt"
git -C "$PROJ" add -A
git -C "$PROJ" diff --cached --stat > "$WORK/out/project.diffstat"
git -C "$PROJ" diff --cached > "$WORK/out/project.diff"
cp -r "$PROJ/.team" "$WORK/out/.team"
cleanup
rm -f "$WORK/out/.team/.gitignore" "$WORK/out/.team"/locks/*.lock
cp "$WORK/prompt.md" "$WORK/out/PROMPT.md"

say "scrubbing"
SCRUB_SECRETS="$KEY" REAL_HOME=$REAL_HOME python3 "$HERE/scrub.py" "$WORK/out" "$WORK" "$PROJ"
rc=$?
say "result in $WORK/out (scrub rc=$rc). Work dir kept: $WORK"
exit $rc
