# Hermes Crew

[![CI](https://github.com/henny-bee/Hermes-Crew/actions/workflows/ci.yml/badge.svg)](https://github.com/henny-bee/Hermes-Crew/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/henny-bee/Hermes-Crew)](https://github.com/henny-bee/Hermes-Crew/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](docs/install.md)

The tmux **agent-team add-on for [Hermes Agent](https://hermes-agent.nousresearch.com)** —
Claude Code–style teams, with real teammates, tasks, file ownership and evidence.

Open one Hermes and ask it to *"create a team with a Planner, an Engineer and a Supervisor"*.
Hermes splits the terminal and starts one **interactive Hermes per teammate**, each in its own
labelled pane. They share a task list, message each other and report back to the lead.

**Why** (instead of `delegate_task`):
- **Visible panes and true parallel sessions** — one interactive Hermes per teammate in its own
  labelled tmux pane; you can watch, talk to, and interrupt any of them.
- **Ownership** — one owner per file, enforced before writes; approvals are bound to file content.
- **Evidence and done-check** — `hermes-crew verify` records results; the lead may only report
  "done" when `hermes-crew status --done-check` passes.

```
┌──────────────────────────┬──────────── Planner ────────────┐
│ lead (you talk here)     │ [from lead] T1: plan ...        │
│                          ├──────────── Engineer ───────────┤
│ [from Engineer] T2 done  │ T2 in progress: write hello.txt │
│ [from Supervisor] ok     ├──────────── Supervisor ─────────┤
│                          │ [from Engineer] please review   │
└──────────────────────────┴─────────────────────────────────┘
```

## Quick start

```bash
./install.sh && source ~/.bashrc     # Windows: .\install.ps1 in PowerShell (see docs/install.md)
hermes-crew doctor                   # verify the installation
hermes-tmux                          # start Hermes once, answer its first-run questions, then:
# paste: "Create a team with a Planner, an Engineer and a Supervisor. Goal: add a README."
```

Windows: `hermes-wsl` instead of `hermes-tmux`. Full walkthrough with a ready-to-paste prompt: [Usage](docs/usage.md).

## Requirements

| | |
|---|---|
| OS | **Windows 10/11 with WSL2** (Ubuntu), or **Linux**. macOS is not supported |
| Tools | `tmux` ≥ 3.0, `python3` ≥ 3.10, `curl`, `git` (the installer adds tmux via apt) |
| Hermes | Installed automatically into WSL/Linux if missing; **classic CLI** (the panes start it with `--cli`) |
| Tested with | <!-- compat:start -->Hermes 0.19.0, 0.21.5<!-- compat:end --> |
| Model | Any model Hermes can use (configured during Hermes setup) |

## It is an add-on

Hermes' own code is never modified. Everything goes into Hermes' documented extension points
(scripts in `~/.local/bin/`, two skills, the `hermes-crew` plugin, one `plugins.enabled` entry),
and uninstall removes it again. Details: [architecture](docs/architecture.md#design-rule).

## Documentation

| Doc | Contents |
|---|---|
| [Install](docs/install.md) | Windows/WSL and Linux installers, options, first run, uninstall |
| [Usage](docs/usage.md) | the team prompt, tmux controls |
| [Commands](docs/commands.md) | every `hermes-crew` and script command |
| [Environment variables](docs/env.md) | all variables and their defaults |
| [Architecture](docs/architecture.md) | how it works, `.team/` files, repository layout, known limitations |
| [Hermes API contract](docs/hermes-api-contract.md) | the Hermes hooks and `ctx` methods the plugin depends on |
| [Metrics](docs/metrics.md) | `hermes-crew status --json` `metrics`: task wall-clock, reopens, tokens per role |
| [Troubleshooting](docs/troubleshooting.md) | `hermes-crew doctor` and common problems |
| [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) | tests, PR conventions, release notes |

## Known limitations

- **Linux / WSL only**; tmux required. macOS is not supported.
- **Classic CLI only** — the TUI interface has no plugin message injection.
- **The terminal guard is best-effort**: file tools are checked exactly, shell commands are pattern-matched
  and scripts, interpreters and `$(…)` are invisible to it. Ownership is a guard rail for cooperating
  agents, not a security boundary.
- **Depends on Hermes' plugin API** (verified with Hermes 0.21.5); `hermes-crew doctor` checks it.
- Agents on the same model share blind spots; configure `--models` for meaningful reviews.

Full list and details: [architecture](docs/architecture.md#known-limitations).

## License

[MIT](LICENSE) © 2026 henny-bee. Hermes Agent itself is a separate project with its own license.
