#!/usr/bin/env bash
# Hermes tmux team - installer for WSL / Linux.
# usage: ./install.sh [--share-windows-config] [--models "<model> <model>..."] [--uninstall]
#   --share-windows-config  (WSL only) use the same config/keys/skills/memories as Windows Hermes
#   --models "a b"          models teammates may use (enables different-model reviewers)
#   --uninstall             remove the team scripts, plugin and skills (Hermes itself is kept)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN="$HOME/.local/bin"
CONF="$HOME/.config/hermes-team.conf"
HH="${HERMES_HOME:-$HOME/.hermes}"
TOOLS="hermes-shared hermes-tmux hermes-crew hermes-team-lib hermes-team-spawn hermes-team-msg hermes-team-board"
SKILLS="hermes-tmux-team hermes-tmux-teammate"
PLUGIN="hermes-crew"
MARK="# hermes-tmux-team"
SHARE=0; UNINSTALL=0; MODELS=""
set_conf() {  # set KEY=VALUE in $CONF, keeping other keys
  mkdir -p "$(dirname "$CONF")"; touch "$CONF"
  grep -v "^$1=" "$CONF" > "$CONF.tmp" || true
  printf '%s=%q\n' "$1" "$2" >> "$CONF.tmp"; mv "$CONF.tmp" "$CONF"; }
while [ $# -gt 0 ]; do a=$1; shift; case "$a" in
  --models) { [ $# -ge 1 ] && [ -n "$1" ] && [ "${1#-}" = "$1" ]; } || { echo "--models needs a value, e.g. --models \"model-a model-b\"" >&2; exit 2; }
            MODELS=$1; shift ;;
  --share-windows-config) SHARE=1 ;;
  --uninstall) UNINSTALL=1 ;;
  -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
  *) echo "unknown option: $a" >&2; exit 2 ;;
esac; done
hermes_cmd() { if [ -x "$BIN/hermes" ]; then echo "$BIN/hermes"; else command -v hermes || true; fi; }
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$UNINSTALL" = 1 ]; then
  HC=$(hermes_cmd); [ -z "$HC" ] || "$HC" plugins disable "$PLUGIN" </dev/null >/dev/null 2>&1 || true
  for t in $TOOLS; do rm -f "$BIN/$t"; done
  rm -rf "$HH/plugins/$PLUGIN" "$CONF"
  for k in $SKILLS; do rm -rf "$HH/skills/autonomous-ai-agents/$k"; done
  if [ -f "$HOME/.bashrc" ]; then   # drop every MARK line with the line after it (and a blank line before it)
    awk -v m="$MARK" 'skip{skip=0; next} $0==m{skip=1; if(held&&prev==""){held=0}; next} {if(held)print prev; prev=$0; held=1} END{if(held)print prev}' \
      "$HOME/.bashrc" > "$HOME/.bashrc.hermes-tmp" && cat "$HOME/.bashrc.hermes-tmp" > "$HOME/.bashrc"; rm -f "$HOME/.bashrc.hermes-tmp"
  fi
  say "Uninstalled. Hermes itself and any shared-config links were left in place."
  exit 0
fi

# 1) prerequisites
for c in curl git; do command -v "$c" >/dev/null || die "'$c' is required (sudo apt install $c)"; done
if ! command -v tmux >/dev/null || ! command -v flock >/dev/null; then
  say "Installing tmux (needs sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq tmux util-linux
fi

tv=$(tmux -V | grep -o '[0-9][0-9]*\.[0-9]*' | head -1 || true)   # empty for a git build: accept
[ -z "$tv" ] || [ "${tv%%.*}" -ge 3 ] || die "tmux >= 3.0 is required (found $(tmux -V)); upgrade it, e.g. sudo apt install tmux"

if ! { command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; }; then
  die "python3 >= 3.10 is required for hermes-crew (sudo apt install python3)"
fi

# 2) Hermes Agent (native Linux install). A wrapper that just calls the Windows hermes.exe
#    cannot split tmux panes, so it is moved aside.
if [ -f "$BIN/hermes" ] && grep -q 'hermes.exe' "$BIN/hermes" 2>/dev/null; then
  say "Moving old Windows-exe wrapper aside: $BIN/hermes.win-wrapper.bak"
  mv "$BIN/hermes" "$BIN/hermes.win-wrapper.bak"
fi
if [ ! -x "$BIN/hermes" ] && FOUND=$(command -v hermes); then
  say "Using Hermes found at $FOUND"
elif [ ! -x "$BIN/hermes" ]; then
  say "Installing Hermes Agent (official installer)"
  INST=$(mktemp); trap 'rm -f "$INST"' EXIT
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$INST"
  if [ "$SHARE" = 1 ]; then bash "$INST" --non-interactive   # settings come from Windows
  else bash "$INST"; fi                                       # runs setup (model, keys)
fi
command -v hermes >/dev/null || [ -x "$BIN/hermes" ] || die "Hermes install failed (expected $BIN/hermes)"

# 3) optional: share settings with Windows Hermes
if [ "$SHARE" = 1 ]; then
  command -v wslpath >/dev/null || die "--share-windows-config only works inside WSL"
  LAD=$(cd /mnt/c && cmd.exe /c 'echo %LOCALAPPDATA%' 2>/dev/null | tr -d '\r')
  WIN_HOME="$(wslpath "$LAD")/hermes"
  [ -f "$WIN_HOME/config.yaml" ] || die "No Windows Hermes found at $WIN_HOME (install Hermes on Windows first, or drop --share-windows-config)"
  mkdir -p "$(dirname "$CONF")" "$HH/.pre-share-backup"
  set_conf WIN_HOME "$WIN_HOME"
  # Files and folders that are shared. Databases/sessions/logs stay separate on purpose:
  # SQLite across the Windows/WSL file boundary can corrupt.
  for item in config.yaml .env auth.json SOUL.md skills memories; do
    [ -e "$WIN_HOME/$item" ] || continue
    if [ -e "$HH/$item" ] && [ ! -L "$HH/$item" ]; then mv "$HH/$item" "$HH/.pre-share-backup/$item.$(date +%s)"; fi
    ln -sfn "$WIN_HOME/$item" "$HH/$item"
  done
  say "Sharing settings with Windows Hermes at $WIN_HOME"
fi

# 4) team scripts + skill
mkdir -p "$BIN"
for t in $TOOLS; do install -m 755 "$HERE/bin/$t" "$BIN/$t"; done
[ -n "$MODELS" ] && set_conf HERMES_TEAM_MODELS "$MODELS"
for k in $SKILLS; do   # every skill under skill/, replaced as a whole
  [ -d "$HERE/skill/$k" ] || continue
  rm -rf "${HH:?}/skills/autonomous-ai-agents/$k"; mkdir -p "$HH/skills/autonomous-ai-agents"
  cp -r "$HERE/skill/$k" "$HH/skills/autonomous-ai-agents/$k"
done
rm -rf "${HH:?}/plugins/$PLUGIN"; mkdir -p "$HH/plugins"; cp -r "$HERE/plugin/$PLUGIN" "$HH/plugins/$PLUGIN"
find "$HH/plugins/$PLUGIN" -name __pycache__ -type d -prune -exec rm -rf {} +
HC=$(hermes_cmd)
if [ -n "$HC" ] && "$HC" plugins enable "$PLUGIN" </dev/null; then :
else echo "WARNING: could not enable the plugin; run: hermes plugins enable $PLUGIN" >&2; fi
grep -q "$MARK" "$HOME/.bashrc" 2>/dev/null || printf '\n%s\nalias hermes=hermes-shared\n' "$MARK" >> "$HOME/.bashrc"
# shellcheck disable=SC2016  # the single quotes are intentional: $HOME must stay literal in .bashrc
case ":$PATH:" in *":$BIN:"*) ;; *) grep -q 'local/bin' "$HOME/.bashrc" || printf '\n%s\nexport PATH="$HOME/.local/bin:$PATH"\n' "$MARK" >> "$HOME/.bashrc" ;; esac

say "Installed: $TOOLS"
say "Plugin:    $HH/plugins/$PLUGIN (enabled in Hermes)"
say "Skills:    $SKILLS"
echo
echo "Start the lead:  hermes-tmux      (from Windows: hermes-wsl)"
echo "Then ask it:     Create a team with 3 teammates: 1. Planner ... 2. Engineer ... 3. Supervisor ..."
echo "Check the setup:  hermes-crew doctor"
