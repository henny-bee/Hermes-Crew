# Changelog

All notable changes to Hermes Crew are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Worked examples (`examples/`) with real team logs, each with a `LESSONS.md`; "what a team costs" in
  `docs/architecture.md`.
- Terminal commands that look like writes but cannot be classified by the guard are now visible as
  rate-limited `board.unparsed` events (`hermes-crew log --kind board.unparsed`).
- `hermes-crew doctor` reports whether the installed Hermes version is tested, untested or known
  broken; the plugin reports missing Hermes hooks as `plugin_degraded` (shown by `status`).
- README requirements table shows the tested Hermes versions, generated from `compat.KNOWN` by
  `scripts/gen-compat-row.py` (checked in CI).
- `hermes-crew status --json` gains a `metrics` object (wall-clock per task, reopen count, tokens per
  role); see `docs/metrics.md`.

### Changed

- Locking in the shell scripts goes through the new `hermes-crew lock <name|path> -- <cmd…>`; the
  `flock` binary is no longer required (removed from the installer's apt list and the requirements).

## [3.0.0] - 2026-10-11

First release with the `hermes-crew` Hermes plugin. Supported platforms: Linux and Windows via
WSL2 (macOS is not supported yet).

### Added

- **`hermes-crew` plugin** for Hermes Agent (public plugin API, stdlib-only, Python >= 3.10).
  Inert unless `HERMES_TEAM_DIR` and `HERMES_TEAM_ROLE` are set. It provides:
  - hook-based **agent state tracking** (`idle / busy / blocked / exited`, heartbeat, last error,
    token usage) written to `.team/agents/<Role>.json`;
  - a file-based **mailbox** whose messages are injected as one new turn only while the receiving
    agent is idle; messages carry sender and time, expire after an hour and are never delivered twice;
  - an **ownership guard** for `write_file`, `patch` and recognised shell writes: files owned by
    another role are blocked, unowned files are claimed by the first writer
    (`HERMES_CREW_AUTO_OWN=0` disables auto-claim);
  - a **watchdog** (lead only) that alerts on human-prompt waits longer than 3 minutes, dead
    agents (their tasks become orphaned), failed turns, lost messages and a stalled team.
- **`hermes-crew` CLI**: `status [--json] [--done-check]`, `watch`, `send`, `inbox`, `task`
  (`add`, `claim`, `start`, `block`, `unblock`, `done`, `assign`, `reopen`, `list`, `show`),
  `verify`, `board`, `log`, `doctor`.
- **Task registry** (`.team/tasks.jsonl`) with owners, priorities, dependencies and handoff notes
  (`.team/handoff/<id>.md`).
- **Evidence**: `hermes-crew verify <task> -- <cmd>` records command, exit code and output tail in
  `.team/verify.jsonl`.
- **Done-check**: `hermes-crew status --done-check` exits 0 only if every task is done, every owned
  file has a current approval from a non-owner and the latest verify of every task passed.
- **Board** (`.team/board.log`): shared decisions, one owner per file, approvals bound to file
  content (shown as STALE when the file changes).
- **Skills** `hermes-tmux-team` (lead) and `hermes-tmux-teammate`; the lead preloads its skill and
  every teammate gets a per-role brief file in `.team/briefs/`.
- **Scripts**: `hermes-tmux`, `hermes-team-spawn` (spawn, `--restart`, `--kill`, per-teammate
  `--model`), `hermes-team-msg`, `hermes-team-board`, `hermes-shared`, `hermes-crew`.
- **Installers** for Linux/WSL (`install.sh`) and Windows (`install.ps1`, with
  `-ShareWindowsConfig`, `-Distro`, `-Models`, `-Uninstall`); `hermes-crew doctor` verifies the
  installation.
- Test suites: a bash suite (`tests/run.sh`, isolated tmux server and `tests/fake-hermes`) and
  pytest (`tests/py`).

### Changed

- Messages are delivered through the plugin mailbox when it is enabled. Without the plugin the
  previous v2 behaviour remains as a fallback: messages are typed into the target pane and the
  board works, but there are no tasks, verify, done-check, guard or watchdog.

### Fixed

- Board races and idle detection; stable per-teammate session names (`team-<hash>-<Role>`).
- Handoff notes are stored under `.team/handoff/`; `HERMES_HOME` is passed to teammate panes.
- The installer restores the shared-config link for `--share-windows-config`.
