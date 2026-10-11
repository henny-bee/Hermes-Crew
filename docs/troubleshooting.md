# Troubleshooting

First run **`hermes-crew doctor`** — it prints `OK|WARN|FAIL` per check (Python, tmux, hermes, plugin
installed and version, plugin enabled, both skills, `.team` writable, agents alive, and the installed Hermes version).

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

## Hermes version and degraded mode

`hermes-crew doctor` has a **hermes version** line (after **hermes**) that compares the installed
Hermes with the versions Hermes Crew was tested with (`KNOWN` in `hermes_crew/compat.py`; the
version comes from `<HERMES_HOME>/hermes-agent/install-stamp.json`, else from `hermes --version`):

| Doctor shows | Meaning |
|---|---|
| `OK` `<v> (tested)` | this Hermes version is known to work |
| `WARN` `<v> untested with hermes-crew <x.y.z>` | not tested; it may work, report what you see |
| `FAIL` `<v> is known broken` | upgrade or downgrade Hermes to a version listed as tested |
| `WARN` `cannot determine the installed Hermes version` | the version could not be read; check `hermes --version` |

At startup the plugin checks that every hook it needs (and `ctx.inject_message`) exists in the running
Hermes. Anything missing puts the agent in **degraded mode**: it is written as `plugin_degraded: [...]`
to `.team/agents/<Role>.json`, logged once, and `hermes-crew status` shows
`WARN <Role>: plugin degraded, Hermes lacks <names>`. If `inject_message` is missing, mail stays
pending (`hermes-crew inbox` still shows it) instead of being delivered. Fix: upgrade or downgrade Hermes
to a version listed as tested (see the README requirements). What the plugin needs from Hermes:
[hermes-api-contract.md](hermes-api-contract.md).

See also [install](install.md), [commands](commands.md), [environment variables](env.md) and the
known limitations in [architecture](architecture.md#known-limitations).
