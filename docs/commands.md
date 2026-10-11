# Commands

## `hermes-crew` (used by the agents; you can run it inside the team window too)

| Command | What it does |
|---|---|
| `status [--json] [--done-check]` | agents (state, task, tokens, last error), tasks, owned files + approvals, decisions. `--done-check` exits 0 only if the work is done; `--json` includes [`metrics`](metrics.md) |
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
| `lock <name\|path> -- <command…>` | run a command while holding an exclusive lock ([details](#hermes-crew-lock)) |

Board: `decide [--replaces <#>] "<decision>"` · `own <file>…` · `transfer <file> <Role>` ·
`release <file>` · `approve <file> "<what was checked>"` (non-owners only; tied to the file's
current content, shows **STALE** when it changes) · `status`.

### `hermes-crew lock`

`hermes-crew lock <name|path> -- <command…>` runs a command while holding an exclusive lock
(`fcntl` flock, the same implementation the CLI uses for `.team/` state).

- A bare name means `<team dir>/.team/locks/<name>.lock` (letters, digits, `_ . -`); a value
  containing `/` is used as the lock file path (created if missing).
- Exit code = the command's (128+N if killed by signal N, 127 if it cannot be started, 2 without a command).
- The `--` may be omitted.
- Used by `hermes-team-msg` (one delivery at a time per pane) and `hermes-shared` (one config heal at a time).

## Scripts

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

Environment variables that change these commands are listed in [env.md](env.md).
