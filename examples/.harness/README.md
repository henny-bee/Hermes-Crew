# Harness for the worked examples

`run-example.sh` runs ONE real Hermes team on a throwaway copy of a seed project and collects the evidence:

```bash
env -u HERMES_TEAM_DIR -u HERMES_TEAM_ROLE bash examples/.harness/run-example.sh 01-add-readme 20
```

(`NN-name` is a directory under `seed/`, `20` the timebox in minutes.) This is a real session with real model
calls. You need an installed Hermes whose `~/.hermes/config.yaml` points at a working provider.

What it does, all inside a fresh `mktemp -d /tmp/t60.XXXXXX`:

1. creates a temporary `HOME`, `HERMES_HOME` and tmux server; your real `~/.hermes`, `~/.local/bin` and `~/.bashrc`
   are not touched, and the repository's own `.team/` is not used;
2. writes a minimal `config.yaml` with `mkconfig.py` (same model and provider as yours, **no key in any file**: the key
   is handed over as an environment variable, `model.key_env`), turns off memory, MCP servers and update checks,
   limits turns and reasoning effort;
3. installs the plugin and scripts of THIS checkout into the temporary home with `install.sh`;
4. seeds the project (`seed/<name>/`, a git repo with one commit) and starts the lead in a detached tmux session with
   `hermes-shared chat --cli -s hermes-tmux-team --query-file PROMPT.md`;
5. polls `hermes-crew status --done-check` every 20 s, waits 60 s after it passes for the lead's report, or stops at
   the timebox;
6. collects `.team/`, the project diff, `status --json`, the pane screens and a `meta.txt`, kills the tmux server, and
   runs `scrub.py` (replaces machine paths; fails on key values, `Bearer`, `sk-…`, `api_key=`).

The proxy key variable name is hard-coded for the author's setup (`HERMES_CUSTOM_LOCALHOST_4000_API_KEY`); adapt
`KEYVAR` and `mkconfig.py` to yours.

## Seeds

| Seed | Project | Planted on purpose |
|---|---|---|
| `01-add-readme` | `wordfreq.py`, a 30-line word counter, no README | nothing |
| `02-add-tests` | `slugify.py` with `slugify` and `parse_duration`, no tests | `slugify(text, max_len)` can end in a dash: `slugify("hello world", 6) == "hello-"` |
| `03-config-migration` | `app.py` reading `config.ini` | nothing |

The seed files are exactly what the teams were given. They are fixtures: do not lint or "fix" them.
