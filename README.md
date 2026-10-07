# Hermes tmux team

Claude Code–style **agent teams** for [Hermes Agent](https://hermes-agent.nousresearch.com).

Open one Hermes and ask it to *"create a team with a Planner, an Engineer and a Supervisor"*.
Hermes splits the terminal and starts one **interactive Hermes per teammate**, each in its own
labelled pane. Teammates message each other and report back to the lead (orchestrator):

```
┌──────────────────────────┬──────────── Planner ────────────┐
│ lead (you talk here)     │ [from lead] Goal: ...           │
│                          ├──────────── Engineer ───────────┤
│ [from Engineer] done     │ write hello.txt                 │
│ [from Supervisor] ok     ├──────────── Supervisor ─────────┤
│                          │ [from Engineer] please review   │
└──────────────────────────┴─────────────────────────────────┘
```

No Hermes code is changed: it is 4 small shell scripts + 1 Hermes skill on top of tmux.

---

## Requirements

| | |
|---|---|
| OS | **Windows 10/11 with WSL2** (Ubuntu), or **Linux** |
| Tools | `tmux`, `curl`, `git`, `flock` (installer adds tmux/flock via apt) |
| Hermes | Installed automatically into WSL/Linux if missing |
| Model | Any model Hermes can use (configured during Hermes setup) |

macOS is not supported (`flock` and `/proc`-style process lookup are Linux-only).

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

Options: `-Distro <name>` to target a specific WSL distro, `-Uninstall` to remove,
`-Models "model-a model-b"` to list the models teammates may use (see *Model diversity* below).

The installer:
- installs `tmux` (asks for your Linux sudo password) and Hermes Agent inside WSL,
- copies the scripts to `~/.local/bin` in WSL and the skill into Hermes' skills,
- creates **`hermes-wsl`** in `%USERPROFILE%\.local\bin` and adds that folder to your PATH.

Open a **new** terminal afterwards.

### Linux / inside WSL directly

```bash
./install.sh                         # or: ./install.sh --share-windows-config  (WSL only)
                                     # add --models "model-a model-b" for different-model reviewers
source ~/.bashrc
```

---

## First run (once)

Start Hermes once and finish anything it asks (model choice, the telemetry question):

```powershell
hermes-wsl          # Windows
```
```bash
hermes-tmux         # Linux / WSL shell
```

Teammates are started automatically and cannot answer first-run questions, so do this first.

---

## Usage

```powershell
cd C:\path\to\your\project
hermes-wsl
```

Then paste a prompt like:

```
Create a team called "lead" with 3 teammates and you as the orchestrator.

1. Planner: Read the project and break the goal into small, concrete steps with clear
   done-criteria. Write the plan to PLAN.md, then hand it to the Engineer.

2. Engineer: Implement the plan step by step. Edit the files, run the code/tests to
   verify, then ask the Supervisor for a review.

3. Supervisor: Review the Engineer's changes against PLAN.md. Approve, or send the
   Engineer a short list of fixes. Report the final result to the lead.

Goal: Add a README.md to this project that explains what it does, how to install it,
and how to run it.

Hand-off order: Planner → Engineer → Supervisor → lead. When everything is approved,
give me a short summary of what changed.
```

Only change the **Goal** line for other jobs. When done, type `stop the team`.

### Controls (tmux)

| Action | Keys |
|---|---|
| Talk to a teammate | click its pane (mouse is on) or `Ctrl+b` + arrow |
| Zoom a pane / unzoom | `Ctrl+b` `z` |
| Leave, agents keep running | `Ctrl+b` `d` — run `hermes-wsl` in the same folder to re-attach |
| Kill everything | `wsl tmux kill-session -t hermes-<folder>` |
| Copy text with mouse on | hold `Shift` while selecting |

### Commands (used by Hermes, also usable by you inside the tmux window)

| Command | What it does |
|---|---|
| `hermes-wsl [args]` | (Windows) open WSL Hermes in tmux as `lead`, in the current folder |
| `hermes-tmux [args]` | (WSL/Linux) same thing from a Linux shell |
| `hermes-team-spawn [--model M] <Role> "<description>"` | split the window, start a Hermes teammate with that role (optionally on another model) |
| `hermes-team-spawn --models` | models available for teammates |
| `hermes-team-spawn --kill <Role>` / `--kill --all` | stop one / all teammates |
| `hermes-team-msg <Role\|lead> "<text>"` | queue a message; typed in when the target is idle (shows send time) |
| `hermes-team-board decide [--replaces <#>] "<decision>"` | record a shared decision (names, interfaces, formats, numbers); replaced ones disappear from `status` |
| `hermes-team-board own <file>` | claim a file; only its owner edits it |
| `hermes-team-board approve <file> "<what was checked>"` | approve the file's *current content* (non-owners only) |
| `hermes-team-board status` | team state, decisions, owners, approvals (`OK` / `STALE`) |
| `hermes-team-msg --list` | show the team (pane id + role) |
| `hermes-shared [args]` | plain Hermes (heals shared-config links first) |

Extra args go to Hermes, e.g. `hermes-wsl -c` resumes the last session.

---

## How it works

1. **`hermes-tmux`** starts Hermes inside a tmux session named `hermes-<folder>` and tags its pane
   with the tmux option `@role=lead`. Pane borders show each pane's role.
2. The **`hermes-tmux-team` skill** tells Hermes: when the user asks for a team, run
   `hermes-team-spawn` per role, assign work with `hermes-team-msg`, then *end your turn* and wait
   for `[from …]` reports (instead of using hidden `delegate_task` subagents).
   It is marked `platforms: [linux]`, so a Windows Hermes sharing the same skills folder ignores it.
3. **`hermes-team-spawn`** finds the caller's tmux pane (walks up the process tree), splits the window,
   starts `hermes-shared` in the new pane, tags it `@role=<Role>`, arranges *lead left / team right*
   and queues the teammate's brief (role + how to report).
4. **`hermes-team-msg`** is the mailbox. Hermes has no API to inject a message into a running
   interactive session, so the message is **typed into the target pane** — but only when that Hermes
   is idle. It watches the prompt line: `❯ …` = idle, `… msg=interrupt …` = busy (typing then would
   interrupt the turn). Delivery runs in the background, one message at a time per pane (`flock`),
   prefixed with sender and send time: `[from Engineer sent 14:02:11] …`.
5. **`hermes-team-board`** keeps the team honest without assuming any roles. It is an append-only
   log in `<project>/.team/board.log`:
   - **decisions** — the single source of truth for anything several files must agree on;
   - **ownership** — one owner per file; others send the owner change requests;
   - **approvals bound to content** — an approval stores the file's hash and *what was checked*;
     owners cannot approve their own files; when the file changes the approval shows **STALE**.

   Every teammate's brief contains the same role-agnostic protocol (check decisions, own your files,
   review real content, never claim unrun checks, no acknowledgement-only messages). The lead may only
   report "done" when `hermes-team-board status` shows every deliverable with an OK approval from a
   non-owner — otherwise it must say what is unverified.
6. **Model diversity.** Agents on the same model share blind spots and tend to agree. With
   `--models` configured, the lead gives reviewing roles a different model than the authors
   (`hermes-team-spawn --model …`); pane borders show each teammate's model. With a single model the
   lead tells you that reviews are same-model.
7. **`hermes-shared`** (only matters with `--share-windows-config`): `config.yaml`, `.env`,
   `auth.json`, `SOUL.md`, `skills/`, `memories/` in `~/.hermes` are symlinks to
   `%LOCALAPPDATA%\hermes`. Hermes saves config files atomically, which turns a symlink into a plain
   file; before every start `hermes-shared` copies a newer WSL copy back to Windows (keeping a
   `*.bak-wsl-*` backup) and restores the link. Databases, sessions and logs are **not** shared —
   SQLite across the Windows/WSL boundary can corrupt.

## Files

```
install.ps1                       Windows installer (calls install.sh in WSL, creates hermes-wsl.cmd)
install.sh                        WSL/Linux installer
bin/hermes-tmux                   start the lead in tmux
bin/hermes-team-spawn             create / stop teammates
bin/hermes-team-msg               idle-aware messaging between panes
bin/hermes-team-board             shared decisions, file ownership, content-bound approvals
bin/hermes-shared                 run Hermes; keep shared-config links healthy
skill/hermes-tmux-team/SKILL.md   teaches Hermes the team workflow
LICENSE                           MIT
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `hermes-wsl` not found | open a new terminal (PATH changed), or check `%USERPROFILE%\.local\bin\hermes-wsl.cmd` |
| Hermes says it is not inside tmux | start it with `hermes-wsl` / `hermes-tmux`, not plain `hermes` |
| Hermes uses hidden subagents instead of panes | say "create a **team** with teammates …"; check the skill exists: `hermes skills list \| grep tmux-team` (new skills load in a new session) |
| A teammate never starts working | it is waiting on a first-run or approval question — click its pane and answer |
| Messages never arrive | panes must use the classic Hermes interface (don't use `--tui` for team sessions) |
| Text from a teammate mixed with what you typed | you typed in the lead pane at the moment a message was delivered; just resend |
| `[exited]` right after `hermes-wsl` | run `wsl -e ~/.local/bin/hermes-shared` to see Hermes' own error |
| Don't run `hermes gateway` in WSL when sharing Windows config | the Windows gateway already uses the same platform tokens — two gateways would both reply |

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```
Removes the scripts, the skill and `hermes-wsl`. Hermes itself (and any shared-config links) stay.
Remove Hermes from WSL with `hermes uninstall` if you want.

## License

[MIT](LICENSE) © 2026 henny-bee. Hermes Agent itself is a separate project with its own license.
