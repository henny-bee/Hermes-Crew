#!/usr/bin/env bash
# Hermes tmux team - installer for WSL / Linux.
# usage: ./install.sh [--share-windows-config] [--models "<model> <model>..."] [--uninstall]
#   --share-windows-config  (WSL only) use the same config/keys/skills/memories as Windows Hermes
#   --models "a b"          models teammates may use (enables different-model reviewers)
#   --uninstall             remove the team scripts and skill (Hermes itself is kept)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN="$HOME/.local/bin"
CONF="$HOME/.config/hermes-team.conf"
HH="${HERMES_HOME:-$HOME/.hermes}"
TOOLS="hermes-shared hermes-tmux hermes-team-spawn hermes-team-msg hermes-team-board"
MARK="# hermes-tmux-team"
SHARE=0; UNINSTALL=0; MODELS=""
set_conf() {  # set KEY=VALUE in $CONF, keeping other keys
  mkdir -p "$(dirname "$CONF")"; touch "$CONF"
  grep -v "^$1=" "$CONF" > "$CONF.tmp" || true
  printf '%s=%q\n' "$1" "$2" >> "$CONF.tmp"; mv "$CONF.tmp" "$CONF"; }
while [ $# -gt 0 ]; do a=$1; shift; case "$a" in
  --models) MODELS=${1:-}; shift ;;
  --share-windows-config) SHARE=1 ;;
  --uninstall) UNINSTALL=1 ;;
  -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
  *) echo "unknown option: $a" >&2; exit 2 ;;
esac; done
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$UNINSTALL" = 1 ]; then
  for t in $TOOLS; do rm -f "$BIN/$t"; done
  rm -rf "$HH/skills/autonomous-ai-agents/hermes-tmux-team" "$CONF"
  sed -i "/$MARK/,+1d" "$HOME/.bashrc" 2>/dev/null || true
  say "Uninstalled. Hermes itself and any shared-config links were left in place."
  exit 0
fi

# 1) prerequisites
for c in curl git; do command -v "$c" >/dev/null || die "'$c' is required (sudo apt install $c)"; done
if ! command -v tmux >/dev/null || ! command -v flock >/dev/null; then
  say "Installing tmux (needs sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq tmux util-linux
fi

# 2) Hermes Agent (native Linux install). A wrapper that just calls the Windows hermes.exe
#    cannot split tmux panes, so it is moved aside.
if [ -f "$BIN/hermes" ] && grep -q 'hermes.exe' "$BIN/hermes" 2>/dev/null; then
  say "Moving old Windows-exe wrapper aside: $BIN/hermes.win-wrapper.bak"
  mv "$BIN/hermes" "$BIN/hermes.win-wrapper.bak"
fi
if [ ! -x "$BIN/hermes" ]; then
  say "Installing Hermes Agent (official installer)"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/hermes-install.sh
  if [ "$SHARE" = 1 ]; then bash /tmp/hermes-install.sh --non-interactive   # settings come from Windows
  else bash /tmp/hermes-install.sh; fi                                         # runs setup (model, keys)
fi
[ -x "$BIN/hermes" ] || die "Hermes install failed (expected $BIN/hermes)"

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
SK="$HH/skills/autonomous-ai-agents/hermes-tmux-team"
mkdir -p "$SK" && cp "$HERE/skill/hermes-tmux-team/SKILL.md" "$SK/SKILL.md"
grep -q "$MARK" "$HOME/.bashrc" 2>/dev/null || printf '\n%s\nalias hermes=hermes-shared\n' "$MARK" >> "$HOME/.bashrc"
case ":$PATH:" in *":$BIN:"*) ;; *) grep -q 'local/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" ;; esac

say "Installed: $TOOLS"
say "Skill:     $SK/SKILL.md"
echo
echo "Start the lead:  hermes-tmux      (from Windows: hermes-wsl)"
echo "Then ask it:     Create a team with 3 teammates: 1. Planner ... 2. Engineer ... 3. Supervisor ..."
