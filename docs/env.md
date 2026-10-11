# Environment variables

| Variable | Meaning |
|---|---|
| `HERMES_TEAM_MAX` | max teammates (default 6) |
| `HERMES_TEAM_MODELS` | models teammates may use; set via `--models` in `~/.config/hermes-team.conf` |
| `HERMES_CREW_AUTO_OWN=0` | don't auto-claim files on first write |
| `HERMES_TEAM_STOP_WAIT` | seconds `--kill` waits for a graceful `/exit` (default 10) |
| `HERMES_TEAM_MSG_TIMEOUT` | typed-in fallback only: seconds before an undeliverable message is dropped (default 3600) |
| `HERMES_TEAM_PROMPT_SYMBOL` | fallback only: prompt symbol for custom skins (default `❯`) |
| `HERMES_TEAM_HERMES` | path to the `hermes` binary to run |

## Set by Hermes Crew itself

These are exported into every pane by `hermes-tmux` / `hermes-team-spawn`; you normally do not set them.
The plugin stays inert unless **both** `HERMES_TEAM_DIR` and `HERMES_TEAM_ROLE` are set.

| Variable | Meaning |
|---|---|
| `HERMES_TEAM_DIR` | the project folder that holds `.team/` |
| `HERMES_TEAM_ROLE` | this pane's role (`lead`, `Planner`, …) |
| `HERMES_CREW_SESSION` | name of the teammate's Hermes session (`team-<hash>-<Role>`) |
| `HERMES_HOME` | your Hermes home (default `~/.hermes`); a custom value is passed through to teammate panes |

## Development / internal

| Variable | Meaning |
|---|---|
| `HERMES_CREW_LIB` | directory containing the `hermes_crew` package; `bin/hermes-crew` otherwise looks in the checkout and the installed plugin |
| `HERMES_TEAM_MSG_POLL` | seconds between mailbox polls in `hermes-team-msg` (default 2; tests lower it) |
| `HERMES_TEAM_RAW` | `hermes-team-msg` internal: send the text without the `[from … sent …]` prefix |
