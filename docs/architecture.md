# Architecture

## Design rule

**It is an add-on. Hermes' own code is never modified.** Everything goes into
Hermes' documented extension points and uninstall removes it again:

| Piece | Installed to |
|---|---|
| Scripts (`hermes-tmux`, `hermes-team-*`, `hermes-crew`, …) | `~/.local/bin/` |
| Skills `hermes-tmux-team` (lead) and `hermes-tmux-teammate` | `~/.hermes/skills/` |
| Plugin `hermes-crew` | `~/.hermes/plugins/hermes-crew/` |
| Config | one entry, `hermes-crew`, in `plugins.enabled` (`hermes plugins enable hermes-crew`) |

## How it works

1. **`hermes-tmux`** starts Hermes in a tmux session `hermes-<folder>-<6-hex hash of the full path>`
   with the lead skill preloaded (`-s hermes-tmux-team`), tags the pane `@role=lead` and sets
   `HERMES_TEAM_DIR` / `HERMES_TEAM_ROLE`; pane borders show role and model.
2. **`hermes-team-spawn`** splits the window (lead left, team right) and starts
   `hermes chat --cli` in the new pane with the **`hermes-tmux-teammate`** skill preloaded and a
   per-teammate **brief file** (`.team/briefs/<Role>.md`: role, team, protocol). The session is named
   (`team-<hash>-<Role>`), so `--restart` resumes the same conversation (and keeps the teammate's model).
3. **The `hermes-crew` plugin** is loaded in every pane but stays inert unless the pane is a team
   member (`HERMES_TEAM_DIR` + `HERMES_TEAM_ROLE`). It uses Hermes' public plugin API
   (every hook and `ctx` method it relies on: [hermes-api-contract.md](hermes-api-contract.md)):
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

## Allow by uncertainty: `board.unparsed`

The terminal guard is best-effort. It blocks only when a target clearly resolves to a file another
role owns; a command it cannot classify is **allowed**. To make that blind spot visible, the plugin
logs a `board.unparsed` event whenever it allows a terminal command that looks like it writes but
has no target the guard could check.

"Looks like it writes" means:

- a write redirection (`>`, `>>`, `>|`, `&>`, `&>>`; not fd duplication like `2>&1`),
- `sed -i` / `--in-place`,
- an interpreter run with `-c` / `-e` (python, python3, perl, ruby, node, sh, bash),
- a `$(...)` or backtick substitution,
- a recognised write command (`tee`, `cp`, `mv`, `rm`, `sed`, … — the guard's extractor table) whose
  path is a variable, e.g. `tee "$f"` or `cp x "$DST"`.

Examples: `x=$(rm src/app.py)`, `python -c "open('f','w')"`, `echo $X > $TARGET`, `tee "$f"`.

The event carries the raw command (clipped to 300 characters) and `suppressed`, the number of further
unparsed commands swallowed by the rate limit (at most one event per agent per 60 s). Blocked
commands and commands whose targets were resolved are not logged. See them with
`hermes-crew log --kind board.unparsed`. The command is still allowed — the event is for the lead and
reviewers, so they know where to check ownership by hand.

**Caution:** the event stores the raw command (first 300 characters) in `.team/events.jsonl`, so it can
contain secrets passed on the command line, e.g. `API_KEY=… python -c …`. Treat `events.jsonl` like a
shell history: do not publish a `.team/` directory without scrubbing it.

## What a team costs

Measured on the three worked examples ([01-add-readme](../examples/01-add-readme/),
[02-add-tests](../examples/02-add-tests/), [03-config-migration](../examples/03-config-migration/)). Each team
was lead + Engineer + Reviewer on `claude-fable-5-1` at low reasoning effort, with two tasks (implement, then
review). No task was reopened.

| Example | Start → green done-check | Task wall-clock T1 / T2 | Input tokens (lead / Engineer / Reviewer) | Total in / out |
|---|---|---|---|---|
| 01-add-readme | 82 s | 36 s / 52 s | 224k / 348k / 270k | 0.84 M / 2.9k |
| 02-add-tests | 61 s | 31 s / 39 s | 256k / 230k / 163k | 0.65 M / 3.0k |
| 03-config-migration | 142 s | 71 s / 118 s | 300k / 440k / 620k | 1.36 M / 6.7k |

A small task took a three-agent team 1–2.5 minutes and 0.65–1.4 M input tokens, but only 3–7 k output
tokens. Every role re-reads its prompt (protocol, brief, tool results) on every model call, so input
dominates, and each extra teammate adds a few hundred thousand input tokens even when its job is short.

How to read these numbers:

- **Input tokens include cached prompt tokens.** The plugin counts Hermes' `prompt_tokens`, which includes
  cache reads and writes ([hermes-api-contract.md](hermes-api-contract.md), known gap 6). Most of the input
  is cache re-reads, which providers bill at a fraction of fresh input, so the totals overstate cost.
- **`wall_s` is claimed → done, not work time.** A dependent task gets its owner when it is added, so its
  clock includes the wait for its dependencies. In 03, T2 (the review) shows 118 s, including the wait for T1.
  `totals.wall_s` (71 + 118 = 189 s) is therefore a sum of overlapping durations, longer than the 142 s the
  whole team needed. For elapsed team time, compare the first and last timestamps in `.team/tasks.jsonl`.

What happened in those runs, including what they missed: each example's `LESSONS.md`. Field definitions:
[metrics.md](metrics.md).

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
docs/                               documentation (this folder)
```

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
