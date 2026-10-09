#!/usr/bin/env bash
# Zero-dependency test runner. usage: bash tests/run.sh [test_name...]
# Every test runs in its own subshell with a throwaway HOME and its OWN tmux server
# (TMUX_TMPDIR), with tests/fake-hermes standing in for Hermes. Nothing touches the real
# ~/.hermes, ~/.local/bin or the real tmux server.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN=$(mktemp -d "${TMPDIR:-/tmp}/hermes-tests.XXXXXX"); trap 'rm -rf "$RUN"' EXIT
export LC_ALL=C.UTF-8
for c in tmux flock git; do command -v "$c" >/dev/null || { echo "tests need $c" >&2; exit 2; }; done

# ---------- assertions: print the reason and abort the current test ----------
die() { echo "    FAIL: $*"; exit 1; }
eq() { [ "$1" = "$2" ] || die "${3:-values differ}: expected [$1] got [$2]"; }
has() { [[ $1 == *"$2"* ]] || die "${3:-missing text}: [$2] not in: ${1:0:400}"; }
hasnt() { [[ $1 != *"$2"* ]] || die "${3:-unexpected text}: [$2] in: ${1:0:400}"; }
wait_for() { local n=$(( $1 * 10 )); shift; while [ $n -gt 0 ]; do eval "$*" && return 0; sleep 0.1; n=$((n-1)); done; return 1; }
await() { local s=$1 m=$2; shift 2; wait_for "$s" "$*" || die "timeout: $m"; }

# ---------- environment ----------
new_env() {  # new_env <name> <short id> (short: tmux socket paths are length-limited): fresh HOME, isolated tmux server, fake hermes first in PATH
  T=$RUN/$2; PROJ=$T/proj; mkdir -p "$T/home/.local/bin" "$T/home/.config" "$T/tmux" "$PROJ"
  export HOME=$T/home TMUX_TMPDIR=$T/tmux FAKE_LOG=$T/fake.log HERMES_TEAM_MSG_POLL=0.2
  export PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
  unset TMUX TMUX_PANE HERMES_HOME HERMES_TEAM_MODELS HERMES_TEAM_HERMES HERMES_TEAM_PROMPT_SYMBOL HERMES_TEAM_MSG_TIMEOUT
  unset FAKE_BUSY FAKE_BUSY_TEXT FAKE_PROFILE FAKE_SYMBOL FAKE_MENU FAKE_PLACEHOLDER
  for f in "$ROOT"/bin/*; do ln -s "$f" "$HOME/.local/bin/"; done
  ln -s "$ROOT/tests/fake-hermes" "$HOME/.local/bin/hermes"
  : > "$FAKE_LOG"; N=0
  trap 'tmux kill-server 2>/dev/null' EXIT; }

lead_start() {  # session "t", lead = bash with a "❯ " prompt (so it looks idle) tagged role lead
  tmux new-session -d -s t -x 160 -y 50 -c "$PROJ" "env PS1='❯ ' bash --norc --noprofile -i"
  LEAD=$(tmux list-panes -t t -F '#{pane_id}'); tmux set -p -t "$LEAD" @role lead; tmux set -w -t t @team_dir "$PROJ"
  await 5 "lead prompt" "tmux capture-pane -p -t $LEAD | grep -q ❯"; }

lead_run() {  # run a shell command line inside the lead pane (so scripts find their pane); sets OUT, RC
  rm -f "$T/rc"; tmux send-keys -t "$LEAD" -l "$* >$T/out 2>&1; echo \$? >$T/rc"; tmux send-keys -t "$LEAD" Enter
  await 20 "lead command: $*" "[ -s $T/rc ]"; OUT=$(<"$T/out"); RC=$(<"$T/rc"); }

as_start() {  # as_start <Role> "<cmd>": run cmd in a throwaway pane carrying that role (several may run concurrently)
  local n=$((++N)); local r=$1; shift; rm -f "$T/rc.$n"
  tmux new-window -d -t t: -c "$PROJ" "tmux set -p -t \$TMUX_PANE @role $r; { $*; } >$T/out.$n 2>&1; echo \$? >$T/rc.$n"; LASTN=$n; }
as_wait() { await 20 "pane $1 finished" "[ -s $T/rc.$1 ]"; OUT=$(<"$T/out.$1"); RC=$(<"$T/rc.$1"); }
as_role() { as_start "$@"; as_wait "$LASTN"; }
board() { as_role "$1" "hermes-team-board ${*:2}"; }   # board <Role> <args...>; args are shell-quoted by the caller

fake_pane() {  # fake_pane <Role>: Hermes stand-in started the way hermes-team-spawn does; sets P
  P=$(tmux split-window -d -P -F '#{pane_id}' -t "$LEAD" -c "$PROJ" "hermes-shared --cli"); tmux set -p -t "$P" @role "$1"
  await 5 "fake prompt" "tmux capture-pane -p -t $P | grep -q '[❯»]'"; }
flog() { cat "$FAKE_LOG"; }
lines_of() { awk '$3=="LINE"{print}' "$FAKE_LOG"; }

# ======================= board =======================
test_decide_after_own() {  # regression: grep -c printed "0\n0" when the log had no DECIDE yet
  lead_start; board Eng "own a.txt"; eq 0 "$RC"
  board Eng "decide 'use port 8080'"; eq 0 "$RC" "decide failed: $OUT"; has "$OUT" "decision #1 recorded"
  has "$(<"$PROJ/.team/board.log")" "use port 8080"; }

test_decide_numbering() {
  lead_start; board Eng "decide one"; board Eng "decide two"; has "$OUT" "#2"
  for i in 1 2 3 4 5 6; do as_start "R$i" "hermes-team-board decide d$i"; ids="$ids $LASTN"; done   # concurrent: unique numbers
  for i in $ids; do as_wait "$i"; eq 0 "$RC"; nums="$nums $(grep -o '#[0-9]*' <<<"$OUT" | head -1)"; done
  eq "#3 #4 #5 #6 #7 #8" "$(tr ' ' '\n' <<<"$nums" | sort -u | grep . | xargs)" "concurrent decision numbers"; }

test_decide_replaces_validation() {
  lead_start; board Eng "decide one"; board Eng "decide --replaces 5 two"; eq 1 "$RC"; has "$OUT" "no such decision"
  board Eng "decide --replaces abc two"; eq 1 "$RC"
  board Eng "decide --replaces 1 two"; eq 0 "$RC" "$OUT"; has "$OUT" "decision #2 recorded (replaces #1)"
  board Eng "decide --replaces 1 three"; eq 1 "$RC"; has "$OUT" "already replaced by #2"
  board Eng "decide --replaces 2 three"; eq 0 "$RC"
  board Eng status; hasnt "$OUT" "#1 ["; hasnt "$OUT" "#2 ["; has "$OUT" "#3 ["; }

test_own_race() {  # two panes claim the same file at the same time: exactly one wins
  lead_start
  for i in 1 2 3 4 5 6; do as_start A "hermes-team-board own f$i.txt"; a=$LASTN; as_start B "hermes-team-board own f$i.txt"; b=$LASTN
    as_wait "$a"; ra=$RC; as_wait "$b"; rb=$RC
    eq 1 $(( (ra == 0) + (rb == 0) )) "winners for f$i.txt (rc $ra/$rb)"
    eq 1 "$(grep -c "	OWN	f$i.txt" "$PROJ/.team/board.log")" "OWN lines for f$i.txt"; done; }

test_own_refused_mentions_transfer() {
  lead_start; board A "own f.txt"; board B "own f.txt"; eq 1 "$RC"; has "$OUT" "owned by A"; has "$OUT" "transfer"; }

test_transfer_release() {
  lead_start; board A "own f.txt"
  board B "transfer f.txt B"; eq 1 "$RC" "non-owner transfer"
  board A "transfer f.txt B"; eq 0 "$RC" "$OUT"; board B status; has "$OUT" "owner=B"
  board A "release f.txt"; eq 1 "$RC" "old owner release"
  board B "release f.txt"; eq 0 "$RC" "$OUT"; board B status; has "$OUT" "owner=(none)"
  board A "own f.txt"; eq 0 "$RC" "own after release"
  lead_run "hermes-team-board transfer f.txt C"; eq 0 "$RC" "lead transfer: $OUT"; board C "release f.txt"; eq 0 "$RC"
  board A "transfer nofile.txt B"; eq 1 "$RC" "transfer of unowned file"; }

test_self_approval_refused() {
  lead_start; echo content > "$PROJ/f.txt"; board A "own f.txt"
  board A "approve f.txt 'I looked at everything in this file carefully'"; eq 1 "$RC"; has "$OUT" "must come from someone else"
  board B "approve f.txt short"; eq 1 "$RC"; has "$OUT" "describe what you actually checked"
  board B "approve f.txt 'read the whole file and checked the content'"; eq 0 "$RC" "$OUT"; has "$OUT" "approved f.txt @"; }

test_stale_after_edit() {
  lead_start; echo v1 > "$PROJ/f.txt"; board A "own f.txt"; board B "approve f.txt 'read the whole file and checked the content'"
  board lead status; has "$OUT" "approved by B"; has "$OUT" " OK - "
  echo v2 > "$PROJ/f.txt"; board lead status; has "$OUT" "STALE"; has "$OUT" "no valid approval"; }

test_unowned_approved_file_listed() {
  lead_start; echo x > "$PROJ/orphan.txt"; board B "approve orphan.txt 'read the whole file and checked the content'"
  board lead status; has "$OUT" "orphan.txt"; has "$OUT" "owner=(none)"; has "$OUT" "!! no owner"; }

test_status_concurrent_no_shared_tmpfile() {
  lead_start; echo x > "$PROJ/f.txt"; board A "own f.txt"; board B "approve f.txt 'read the whole file and checked the content'"
  for i in 1 2 3 4 5 6; do as_start "R$i" "hermes-team-board status | grep -v '^  R'"; ids="$ids $LASTN"; done
  for i in $ids; do as_wait "$i"; eq 0 "$RC"; has "$OUT" "approved by B"; has "$OUT" "OK"; done
  ! compgen -G "$PROJ/.team/*.view" >/dev/null || die "leftover .view file"; }

test_team_gitignore() {
  lead_start; git -C "$PROJ" init -q; board A "own f.txt"
  eq '*' "$(<"$PROJ/.team/.gitignore")" ".team/.gitignore"
  hasnt "$(git -C "$PROJ" status --porcelain -uall)" ".team" ".team shows in git status"; }

# ======================= messaging =======================
send() { lead_run "$*"; eq 0 "$RC" "msg failed: $OUT"; }   # send <hermes-team-msg args...> from the lead pane

test_msg_delivered_when_idle() {
  lead_start; fake_pane Eng; send hermes-team-msg Eng "hello there"
  await 10 "delivery" "grep -q 'hello there' $FAKE_LOG"; has "$(flog)" "ARGS --cli"
  has "$(lines_of)" "[from lead sent "
  await 5 "log" "grep -q delivered $PROJ/.team/messages.log"
  m=$(<"$PROJ/.team/messages.log"); has "$m" "	lead	Eng	queued	hello there"; has "$m" "	lead	Eng	delivered	hello there"
  [ -d "$PROJ/.team/locks" ] || die "lock not under .team/locks"; }

test_msg_multiline_is_one_line() {
  lead_start; fake_pane Eng; send hermes-team-msg Eng "\$'line one\\nline two'"
  await 10 "delivery" "grep -q 'line two' $FAKE_LOG"; has "$(lines_of)" "line one line two"; eq 1 "$(lines_of | grep -c 'line one')"; }

busy_case() {  # busy_case <placeholder text>: a second message must wait until the first turn is over
  export FAKE_BUSY=4; [ -n "$1" ] && export FAKE_BUSY_TEXT=$1
  lead_start; fake_pane Eng; send hermes-team-msg Eng first; await 10 "first" "grep -q ' LINE .*first' $FAKE_LOG"
  send hermes-team-msg Eng second; await 15 "second" "grep -q ' LINE .*second' $FAKE_LOG"
  t1=$(awk '/LINE .*first/{print $1}' "$FAKE_LOG"); t2=$(awk '/LINE .*second/{print $1}' "$FAKE_LOG")
  gap=$(awk -v a="$t1" -v b="$t2" 'BEGIN{printf "%.1f", b-a}'); [ "$(awk -v g="$gap" 'BEGIN{print (g>=3.8)}')" = 1 ] || die "second delivered only ${gap}s after first (busy for 4s)"
  hasnt "$(flog)" BUSYLINE "typed into a busy prompt"; }
test_msg_not_delivered_while_busy_english() { busy_case ""; }
test_msg_not_delivered_while_busy_translated() { busy_case "메시지=중단 · /queue · /bg · /steer · Ctrl+C 취소"; }

test_msg_busy_slash_command() { busy_case "⠋ Processing command... "; }   # no /steer in this placeholder

test_msg_delivered_with_profile_prefix() {
  export FAKE_PROFILE=coder; lead_start; fake_pane Eng; send hermes-team-msg Eng "hi coder"; await 10 "delivery" "grep -q 'hi coder' $FAKE_LOG"; }

test_msg_delivered_with_custom_symbol() {
  export FAKE_SYMBOL='»' HERMES_TEAM_PROMPT_SYMBOL='»'; lead_start; fake_pane Eng; send hermes-team-msg Eng "hi skin"; await 10 "delivery" "grep -q 'hi skin' $FAKE_LOG"; }

test_msg_delivered_with_idle_placeholder() {  # dim placeholder text sits AFTER the cursor and must not count as input
  export FAKE_PLACEHOLDER="Type a message or /help"; lead_start; fake_pane Eng; send hermes-team-msg Eng "hi ph"; await 10 "delivery" "grep -q 'hi ph' $FAKE_LOG"; }

test_msg_not_delivered_into_approval_menu_and_sender_told() {
  export FAKE_MENU=1 HERMES_TEAM_MSG_TIMEOUT=3; lead_start; fake_pane Eng
  send hermes-team-msg Eng "should not arrive"
  await 12 "expiry logged" "grep -q expired $PROJ/.team/messages.log"
  hasnt "$(lines_of)" "should not arrive" "message typed into the menu"
  await 12 "sender notified" "tmux capture-pane -p -t $LEAD | grep -q 'expired undelivered'"
  has "$(tmux capture-pane -p -t "$LEAD" -J)" "[hermes-team] message to Eng expired undelivered: should not arrive"; }

test_msg_not_delivered_while_half_typed() {
  lead_start; fake_pane Eng; tmux send-keys -t "$P" -l "abc"
  send hermes-team-msg Eng "after typing"; sleep 2
  hasnt "$(flog)" "after typing" "delivered into half-typed input"
  tmux send-keys -t "$P" C-u; await 10 "delivery after clearing" "grep -q 'after typing' $FAKE_LOG"
  hasnt "$(lines_of)" "abc[from" "typed text got mixed with the message"; }

test_msg_unknown_role() { lead_start; lead_run hermes-team-msg Nobody hi; eq 1 "$RC"; has "$OUT" "no pane with role"; }

test_status_idle_busy() {
  export FAKE_BUSY=6; lead_start; fake_pane Eng
  lead_run hermes-team-board status; has "$(grep Eng <<<"$OUT")" idle
  send hermes-team-msg Eng go; await 10 "go" "grep -q ' LINE .*go' $FAKE_LOG"; sleep 1
  lead_run hermes-team-board status; has "$(grep Eng <<<"$OUT")" busy; }

# ======================= spawn / session =======================
test_spawn_creates_labelled_cli_pane() {
  export HERMES_TEAM_MODELS="m1 m2"; lead_start
  lead_run hermes-team-spawn --model m3 Eng "does engineering"; eq 0 "$RC" "$OUT"; has "$OUT" "WARNING: model 'm3' is not in HERMES_TEAM_MODELS"; has "$OUT" "spawned Eng"
  eq "Eng" "$(tmux list-panes -t t -F '#{@role}' | grep Eng)"
  has "$(tmux list-panes -t t -F '#{pane_start_command}')" "hermes-shared --cli -m m3"
  await 10 "args" "grep -q 'ARGS --cli -m m3' $FAKE_LOG"
  await 15 "brief" "grep -q 'You are Eng, a teammate' $FAKE_LOG"
  eq '*' "$(<"$PROJ/.team/.gitignore")"
  lead_run hermes-team-spawn Rev "reviews"; eq 0 "$RC"; hasnt "$OUT" WARNING
  eq 2 "$(tmux list-panes -t t -F '#{@role}' | grep -vc '^lead$')" "teammates"
  # main pane is half the window width in columns (no "50%": needs tmux >= 3.3)
  eq "$(( $(tmux display -p -t t '#{window_width}') / 2 ))" "$(tmux show -wv -t t main-pane-width)"; }

test_spawn_reserved_roles() {
  lead_start; lead_run hermes-team-spawn lead "x"; eq 2 "$RC"; has "$OUT" reserved
  lead_run hermes-team-spawn USER "x"; eq 2 "$RC"; eq 1 "$(tmux list-panes -t t | wc -l)"; }

test_kill_requires_role_or_all() {
  lead_start; lead_run hermes-team-spawn Eng "e"; lead_run hermes-team-spawn Rev "r"
  tmux split-window -d -t "$LEAD" -c "$PROJ" "sleep 300"     # a pane without @role
  eq 4 "$(tmux list-panes -t t | wc -l)"
  lead_run hermes-team-spawn --kill; eq 2 "$RC"; eq 4 "$(tmux list-panes -t t | wc -l)" "--kill without arg killed something"
  lead_run hermes-team-spawn --kill nobody; eq 1 "$RC"; eq 4 "$(tmux list-panes -t t | wc -l)"
  lead_run hermes-team-spawn --kill eng; eq 0 "$RC"; eq 3 "$(tmux list-panes -t t | wc -l)"
  lead_run hermes-team-spawn --kill --all; eq 0 "$RC"; has "$OUT" "stopped Rev"
  eq "|lead|" "$(tmux list-panes -t t -F '#{@role}' | sort | tr '\n' '|')" "lead and the unlabelled pane must survive"; }

test_session_name_unique_per_path() {
  mkdir -p "$T/a/proj" "$T/b/proj"
  (cd "$T/a/proj" && hermes-tmux </dev/null >/dev/null 2>&1); (cd "$T/b/proj" && hermes-tmux </dev/null >/dev/null 2>&1)   # attach fails without a tty; session stays
  s=$(tmux list-sessions -F '#{session_name}'); eq 2 "$(wc -l <<<"$s")" "sessions: $s"
  while read -r n; do [[ $n =~ ^hermes-proj-[0-9a-f]{6}$ ]] || die "bad session name $n"; done <<<"$s"
  eq "lead lead" "$(tmux list-panes -a -F '#{@role}' | xargs)"
  has "$(tmux list-panes -a -F '#{pane_start_command}' | head -1)" "hermes-shared --cli -s hermes-tmux-team"
  dirs=$(for n in $s; do tmux show -wv -t "$n" @team_dir; done | sort | xargs); eq "$T/a/proj $T/b/proj" "$dirs" "team dirs"; }

test_hermes_tmux_inside_tmux_tags_lead() {
  tmux new-session -d -s t -x 100 -y 30 -c "$PROJ" "sleep 300"
  P=$(tmux new-window -d -P -F '#{pane_id}' -t t: -c "$PROJ" "hermes-tmux extra-arg")
  await 10 "args" "grep -q 'ARGS --cli -s hermes-tmux-team extra-arg' $FAKE_LOG"
  eq lead "$(tmux display -p -t "$P" '#{@role}')"; eq "$PROJ" "$(tmux show -wv -t "$P" @team_dir)"; }

# ======================= installer =======================
test_install_models_needs_value() {
  out=$(bash "$ROOT/install.sh" --models 2>&1); rc=$?; eq 2 "$rc"; has "$out" "--models needs a value"
  out=$(bash "$ROOT/install.sh" --models --uninstall 2>&1); eq 2 "$?"; has "$out" "--models needs a value"; }

test_install_into_temp_home_finds_hermes_on_path() {
  mkdir -p "$T/extra"; ln -s "$ROOT/tests/fake-hermes" "$T/extra/hermes"; rm -f "$HOME/.local/bin"/*     # a HOME that has no ~/.local/bin/hermes
  PATH=$T/extra:$PATH bash "$ROOT/install.sh" --models "m1 m2" >"$T/install.out" 2>&1 || die "install failed: $(<"$T/install.out")"
  has "$(<"$T/install.out")" "Using Hermes found at $T/extra/hermes"
  for t in hermes-shared hermes-tmux hermes-team-lib hermes-team-spawn hermes-team-msg hermes-team-board; do [ -x "$HOME/.local/bin/$t" ] || die "$t not installed"; done
  has "$(<"$HOME/.config/hermes-team.conf")" "HERMES_TEAM_MODELS"
  PATH=$T/extra:$PATH hermes-shared --probe; has "$(<"$FAKE_LOG")" "ARGS --probe"   # hermes-shared falls back to PATH
  bash "$ROOT/install.sh" --uninstall >/dev/null 2>&1; [ ! -e "$HOME/.local/bin/hermes-team-lib" ] || die "lib survived uninstall"; }

# ======================= runner =======================
ALL=$(declare -F | awk '{print $3}' | grep '^test_'); SEL=${*:-$ALL}; pass=0; fail=0; idx=0; failed=""
for t in $SEL; do
  s=$SECONDS
  idx=$((idx+1)); ( new_env "${t#test_}" "$idx"; "$t" ) >"$RUN/$t.out" 2>&1 & tp=$!
  ( sleep "${TEST_TIMEOUT:-120}"; echo "    FAIL: test timed out" >>"$RUN/$t.out"; pkill -P "$tp"; kill "$tp" ) >/dev/null 2>&1 & wd=$!
  if wait "$tp"; then pass=$((pass+1)); echo "PASS $t ($((SECONDS-s))s)"
  else fail=$((fail+1)); failed="$failed $t"; echo "FAIL $t"; sed 's/^/    | /' "$RUN/$t.out"; fi
  kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
done
echo "---- $pass passed, $fail failed${failed:+:$failed}"
[ "$fail" = 0 ]
