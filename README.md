# Hermes Crew

The tmux **agent-team add-on for [Hermes Agent](https://hermes-agent.nousresearch.com)** —
Claude Code–style teams, with real teammates, tasks, file ownership and evidence.

Open one Hermes and ask it to *"create a team with a Planner, an Engineer and a Supervisor"*.
Hermes splits the terminal and starts one **interactive Hermes per teammate**, each in its own
labelled pane. They share a task list, message each other and report back to the lead:

```
┌──────────────────────────┬──────────── Planner ────────────┐
│ lead (you talk here)     │ [from lead] T1: plan ...        │
│                          ├──────────── Engineer ───────────┤
│ [from Engineer] T2 done  │ T2 in progress: write hello.txt │
│ [from Supervisor] ok     ├──────────── Supervisor ─────────┤
│                          │ [from Engineer] please review   │
└──────────────────────────┴─────────────────────────────────┘
```

**Design rule: it is an add-on. Hermes' own code is never modified.** Everything goes into
Hermes' documented extension points and uninstall removes it again:

| Piece | Installed to |
|---|---|
| Scripts (`hermes-tmux`, `hermes-team-*`, `hermes-crew`, …) | `~/.local/bin/` |
| Skills `hermes-tmux-team` (lead) and `hermes-tmux-teammate` | `~/.hermes/skills/` |
| Plugin `hermes-crew` | `~/.hermes/plugins/hermes-crew/` |
| Config | one entry, `hermes-crew`, in `plugins.enabled` (`hermes plugins enable hermes-crew`) |

---

## Requirements

| | |
|---|---|
| OS | **Windows 10/11 with WSL2** (Ubuntu), or **Linux**. macOS is not supported |
| Tools | `tmux` ≥ 3.0, `python3` ≥ 3.10, `curl`, `git`, `flock` (the installer adds tmux/flock via apt) |
| Hermes | Installed automatically into WSL/Linux if missing; **classic CLI** (the panes start it with `--cli`) |
| Model | Any model Hermes can use (configured during Hermes setup) |

---

## Install

### Windows (WSL)

1. If you don't have WSL yet: `wsl --install -d Ubuntu`, reboot, open *Ubuntu* once and create your user.
2. Copy this folder anywhere, open **PowerShell** in it and run **one** of:

```powershell
# A) New WSL Hermes with its own settings (runs Hermes setup: pick model / API key)
powershell -ExecutionPolicy Bypass -File .\install.ps1

# B) You already use Hermes on Windows: share its config, keys, skills and memories
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShareWindowsConfig
```

Options: `-Distro <name>` to target a WSL distro, `-Uninstall` to remove,
`-Models "model-a model-b"` to list the models teammates may use (different-model reviewers).

The installer installs `tmux` and Hermes inside WSL (asks for your Linux sudo password), copies
the scripts, skills and plugin, enables the plugin, and creates **`hermes-wsl`** in
`%USERPROFILE%\.local\bin` (added to your PATH). Open a **new** terminal afterwards.

### Linux / inside WSL directly

```bash
./install.sh                         # or: ./install.sh --share-windows-config  (WSL only)
                                     # add --models "model-a model-b" for different-model reviewers
source ~/.bashrc
hermes-crew doctor                   # verify the installation
```

---

## First run (once)

Start Hermes once and finish anything it asks (model choice, the telemetry question).
Teammates start automatically and cannot answer first-run questions.

```powershell
hermes-wsl          # Windows
```
```bash
hermes-tmux         # Linux / WSL shell
```

---

## Usage

```powershell
cd C:\path\to\your\project
hermes-wsl
```

Then paste a prompt like:

```
Create a team with 3 teammates and you as the orchestrator.

1. Planner: Read the project and break the goal into small, concrete tasks with clear
   done-criteria. Write the plan to PLAN.md, then hand it to the Engineer.

2. Engineer: Implement the tasks one by one. Edit the files, run the code/tests through
   `hermes-crew verify` so the result is recorded, then ask the Supervisor for a review.

3. Supervisor: Review the Engineer's real files against PLAN.md. Approve, or send the
   Engineer a short list of fixes. Report the final result to the lead.

Goal: Add a README.md to this project that explains what it does, how to install it,
and how to run it.

Create the work as tasks (Planner → Engineer → Supervisor, each depending on the one
before). You are done only when `hermes-crew status --done-check` passes; then give me
a short summary of what changed and what was verified.
```

Only change the **Goal** line for other jobs. Tiny jobs don't need a team — the lead will tell you
and just do them. If the lead is restarted, it resumes from `hermes-crew status` instead of
starting over. When done, type `stop the team`.

Watch the team from a second tmux window: `hermes-crew watch`.

### Controls (tmux)

| Action | Keys |
|---|---|
| Talk to a teammate | click its pane (mouse is on) or `Ctrl+b` + arrow |
| Zoom a pane / unzoom | `Ctrl+b` `z` |
| Leave, agents keep running | `Ctrl+b` `d` — run `hermes-wsl` in the same folder to re-attach |
| Kill everything | `wsl tmux kill-session -t hermes-<folder>-<hash>` (see `tmux ls`; the hash is of the full path) |
| Copy text with mouse on | hold `Shift` while selecting |

---

## Commands

### `hermes-crew` (used by the agents; you can run it inside the team window too)

| Command | What it does |
|---|---|
| `status [--json] [--done-check]` | agents (state, task, tokens, last error), tasks, owned files + approvals, decisions. `--done-check` exits 0 only if the work is done |
| `watch [--interval S]` | live status, `Ctrl+C` to quit |
| `send <Role\|lead\|all> "<text>"` | queue a message (delivered when the receiver is idle) |
| `inbox [--all]` | my pending (`--all`: also recent) messages |
| `task add "<title>" [--deps T1,T2] [--owner R] [--priority N] [--desc …]` | create a task |
| `task claim\|start\|unblock <id>`, `task block <id> "<reason>"` | work the task |
| `task done <id> [--note "<text>" \| --handoff FILE]` | finish; the handoff note (what changed, how verified, open issues) is stored in `.team/handoff/<id>.md` |
| `task assign <id> <Role>`, `task reopen <id> "<reason>"` | lead: (re)assign / send back |
| `task list [--mine] [--json]`, `task show <id>` | inspect |
| `verify <task> [--timeout S] -- <cmd…>` | run a command, record exit code + output as evidence; exits with its code |
| `board decide\|own\|transfer\|release\|approve\|status …` | decisions, file ownership, content-bound approvals (below) |
| `log [-n N] [--kind PREFIX] [--role R]` | recent events |
| `doctor [--json]` | check the installation (exit 1 on FAIL) |

Board: `decide [--replaces <#>] "<decision>"` · `own <file>…` · `transfer <file> <Role>` ·
`release <file>` · `approve <file> "<what was checked>"` (non-owners only; tied to the file's
current content, shows **STALE** when it changes) · `status`.

### Scripts

| Command | What it does |
|---|---|
| `hermes-wsl [args]` | (Windows) open WSL Hermes in tmux as `lead`, in the current folder |
| `hermes-tmux [args]` | (WSL/Linux) same from a Linux shell |
| `hermes-team-spawn [--model M] <Role> "<description>"` | split the window and start a teammate (optionally on another model); max `HERMES_TEAM_MAX` (default 6) |
| `hermes-team-spawn --models` | models available for teammates |
| `hermes-team-spawn --restart [--model M] <Role>` | restart a dead/stuck teammate; it resumes its session (needs the plugin) |
| `hermes-team-spawn --kill <Role>` / `--kill --all` | stop one / all teammates gracefully (`/exit`, then kill after 10 s; the lead is never touched) |
| `hermes-team-msg <Role\|lead> "<text>"` | like `hermes-crew send` (mailbox when the target is alive, typed-in fallback otherwise); `--list` shows the team |
| `hermes-team-board …` | same as `hermes-crew board …` |
| `hermes-shared [args]` | plain Hermes (heals shared-config links first) |

Extra args go to Hermes, e.g. `hermes-wsl -c` resumes the last session.

### Environment variables

| Variable | Meaning |
|---|---|
| `HERMES_TEAM_MAX` | max teammates (default 6) |
| `HERMES_TEAM_MODELS` | models teammates may use; set via `--models` in `~/.config/hermes-team.conf` |
| `HERMES_CREW_AUTO_OWN=0` | don't auto-claim files on first write |
| `HERMES_TEAM_STOP_WAIT` | seconds `--kill` waits for a graceful `/exit` (default 10) |
| `HERMES_TEAM_MSG_TIMEOUT` | typed-in fallback only: seconds before an undeliverable message is dropped (default 3600) |
| `HERMES_TEAM_PROMPT_SYMBOL` | fallback only: prompt symbol for custom skins (default `❯`) |
| `HERMES_TEAM_HERMES` | path to the `hermes` binary to run |

---

## How it works

1. **`hermes-tmux`** starts Hermes in a tmux session `hermes-<folder>-<6-hex hash of the full path>`
   with the lead skill preloaded (`-s hermes-tmux-team`), tags the pane `@role=lead` and sets
   `HERMES_TEAM_DIR` / `HERMES_TEAM_ROLE`; pane borders show role and model.
2. **`hermes-team-spawn`** splits the window (lead left, team right) and starts
   `hermes chat --cli` in the new pane with the **`hermes-tmux-teammate`** skill preloaded and a
   per-teammate **brief file** (`.team/briefs/<Role>.md`: role, team, protocol). The session is named
   (`team-<hash>-<Role>`), so `--restart` resumes the same conversation (and keeps the teammate's model).
3. **The `hermes-crew` plugin** is loaded in every pane but stays inert unless the pane is a team
   member (`HERMES_TEAM_DIR` + `HERMES_TEAM_ROLE`). It uses Hermes' public plugin API:
   - **State tracking via hooks** — each agent writes `idle / busy / blocked / exited`, heartbeat,
     last error and token usage to `.team/agents/<Role>.json`. Nothing is read from the screen.
   - **Mailbox, injected only when idle** — `hermes-crew send` writes a file into
     `.team/mail/<Role>/`; the receiver's plugin injects pending messages as **one new turn, only
     while that agent is idle**, so a running turn is never interrupted. Messages carry sender and send time,
     expire after an hour (the sender is told), and are never delivered twice.
   - **Ownership guard** — before `write_file`, `patch` and recognised shell writes, the file is
     checked: files owned by another role are **blocked** and the agent is told whom to ask;
     files nobody owns are claimed by the writer automatically (`HERMES_CREW_AUTO_OWN=0` turns this off).
   - **Watchdog (lead only)** — alerts the lead (as a message, plus a tmux notice) when a teammate
     waits on a human prompt > 3 min, died (its tasks become *orphaned*), failed a turn, lost a
     message, or the whole team has stalled with work still open.
4. **Tasks** (`.team/tasks.jsonl`): id, owner, status (`open → claimed → in_progress → blocked/done`),
   priority, dependencies. A task can only be claimed when its dependencies are done; finishing one
   notifies the owners of the tasks it unblocks.
5. **Evidence** — `hermes-crew verify <task> -- <cmd>` records command, exit code and output tail in
   `.team/verify.jsonl`. "Tests pass" without a record counts as *not verified*.
6. **Done-check** — `hermes-crew status --done-check` exits 0 only if: every task is done; every
   owned file has an OK (non-stale) approval from a non-owner; the latest verify of every task
   that has one passed. The lead may only report "done" when it passes.
7. **Board** (`.team/board.log`, append-only): shared **decisions** (single source of truth for
   names/interfaces/formats), one **owner** per file, and **approvals bound to content** (hash +
   what was checked; owners cannot approve their own files).
8. **Model diversity** — with `--models` configured the lead gives reviewers a different model than
   the authors; with one model it tells you reviews are same-model.
9. **Without the plugin** (not installed / not enabled) everything falls back to the v2 behaviour:
   messages are typed into the target pane when its cursor sits behind an empty prompt
   (`HERMES_TEAM_PROMPT_SYMBOL` overrides `❯`), the board works, but there is no tasks / verify /
   done-check / guard / watchdog.
10. **`hermes-shared`** (only with `--share-windows-config`): `config.yaml`, `.env`, `auth.json`,
    `SOUL.md`, `skills/`, `memories/` in `~/.hermes` are symlinks to `%LOCALAPPDATA%\hermes`.
    Hermes saves config files atomically, which turns a symlink into a plain file; before every start
    `hermes-shared` copies a newer WSL copy back (keeping a `*.bak-wsl-*` backup) and restores the
    link. Databases, sessions and logs are **not** shared (SQLite across the boundary can corrupt).

## Files written in `.team/` (in the project folder; self-ignoring via `.team/.gitignore`)

```
agents/<Role>.json   live state of each agent          mail/<Role>/{new,cur,expired}/   mailbox
tasks.jsonl          task registry (event log)         verify.jsonl      evidence records
board.log            decisions, owners, approvals      events.jsonl      every message/state/task/error event
handoff/<task>.md    handoff notes                     briefs/<Role>.md  teammate briefs
escalations.json     watchdog dedup                    locks/            lock files
```

## Repository layout

```
install.sh / install.ps1            installers (WSL/Linux, Windows → WSL)
bin/hermes-tmux, hermes-team-*      tmux layer: start lead, spawn/kill, messaging, board wrapper
bin/hermes-crew                     CLI launcher (Python)
bin/hermes-shared                   run Hermes; keep shared-config links healthy
plugin/hermes-crew/                 Hermes plugin + the hermes_crew package (stdlib only, Python ≥ 3.10)
skill/hermes-tmux-team/             lead skill        skill/hermes-tmux-teammate/   teammate skill
tests/run.sh, tests/py/             bash suite (isolated tmux + fake Hermes) and pytest
```

---

## Known limitations

- **Linux / WSL only**; tmux required. macOS is not supported.
- **Classic CLI only.** Panes start `hermes chat --cli`; the TUI interface has no plugin message injection.
- **The terminal guard is best-effort.** File tools (`write_file`, `patch`) are checked exactly.
  Shell commands are pattern-matched (`>`/`>>`, `tee`, `sed -i`, `perl -i`, `mv`, `cp`, `rm`,
  `truncate`, `dd of=`, …). It **cannot see**: writes from scripts or interpreters (`python -c`,
  `node -e`, `make`), `$(…)`/backticks and `$VARIABLES` in paths, `xargs`/`find -exec`,
  `git checkout/restore/reset`, `ln`, `install`, editors, `execute_code`/`delegate_task`.
  Ownership is a guard rail for cooperating agents, not a security boundary.
- **Depends on Hermes' plugin API** (verified with Hermes 0.21.5). A Hermes update can change hook or
  injection behaviour; `hermes-crew doctor` checks the installation, and without a working plugin the
  team falls back to typed messages.
- Agents on the same model share blind spots; configure `--models` for reviews that mean something.

---

## Troubleshooting

First run **`hermes-crew doctor`** — it prints `OK|WARN|FAIL` per check (Python, tmux, hermes, plugin
installed and version, plugin enabled, both skills, `.team` writable, agents alive).

| Problem | Fix |
|---|---|
| `hermes-wsl` not found | open a new terminal (PATH changed), or check `%USERPROFILE%\.local\bin\hermes-wsl.cmd` |
| Hermes says it is not inside tmux | start it with `hermes-wsl` / `hermes-tmux`, not plain `hermes` |
| Hermes uses hidden subagents instead of panes | say "create a **team** with teammates …"; check `hermes skills list \| grep tmux-team` (new skills load in a new session) |
| Spawn warns `hermes-crew plugin is installed but not enabled`, or doctor: plugin not enabled / no tasks, verify or guard | `hermes plugins enable hermes-crew`, then restart the team's panes (`hermes-team-spawn --restart <Role>`) |
| A teammate never starts working | it waits on a first-run or approval question — click its pane and answer. The watchdog tells the lead after 3 min which pane |
| Teammate blocked on an approval | answer it in its pane; the lead is alerted automatically and tells you which one |
| A teammate died | the lead restarts it (`--restart`) and reassigns its orphaned tasks |
| Messages never arrive | check `hermes-crew status` (is the agent `idle` and alive?) and `hermes-crew log --kind msg`. Without the plugin: panes need the classic interface and, with a custom prompt skin, `HERMES_TEAM_PROMPT_SYMBOL` |
| `[exited]` right after `hermes-wsl` | run `wsl -e ~/.local/bin/hermes-shared` to see Hermes' own error |
| Don't run `hermes gateway` in WSL when sharing Windows config | the Windows gateway already uses the same platform tokens — two gateways would both reply |

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```
Disables and removes the plugin, removes both skills, all scripts, the `~/.bashrc` lines it added and `hermes-wsl`.
Hermes itself (and any shared-config links) stay. Remove Hermes from WSL with `hermes uninstall` if you want.
`.team/` folders in your projects are left alone (delete them if you like).

## License

[MIT](LICENSE) © 2026 henny-bee. Hermes Agent itself is a separate project with its own license.
