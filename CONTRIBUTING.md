# Contributing to Hermes Crew

Thanks for helping. Hermes Crew is an **add-on** for Hermes Agent: it must never modify Hermes'
own code, only use its documented extension points (plugin hooks, skills, `plugins.enabled`).

## Setup

You need Linux or WSL2 with `tmux` >= 3.0, `python3` >= 3.10, `git` and `curl`. Work on a clone;
never run `install.sh` against your real `~/.hermes` while developing — use a throwaway
`HERMES_HOME` (and `HOME`) instead.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'          # pytest, pytest-cov, ruff, mypy, hypothesis
```

## Tests

There are two suites; run both before you open a PR.

```bash
pytest tests/py -q               # Python: the hermes_crew package and the plugin
bash tests/run.sh                # bash: scripts, tmux layer, installer
bash tests/run.sh <test_name>    # a single bash test
```

### The bash suite and `fake-hermes`

`tests/run.sh` has no dependencies besides `tmux` and `git`. Every test runs in its own
subshell with a throwaway `HOME` and **its own tmux server** (`TMUX_TMPDIR`), so it never touches
your real `~/.hermes`, `~/.local/bin` or tmux sessions. `tests/fake-hermes` stands in for Hermes:
it draws a prompt with the cursor right behind it, logs every argument, environment and input line
to `$FAKE_LOG`, and can simulate a busy agent (`FAKE_BUSY`), approval menus (`FAKE_MENU`) and custom
prompt symbols (`FAKE_SYMBOL`). The header of `tests/fake-hermes` documents all its variables.
To add a bash test, follow the existing ones in `tests/run.sh` (`new_env`, `lead_start`,
`lead_run`, then assert with `eq` / `has` / `await`).

### The Python suite

`tests/py` isolates itself automatically (`conftest.py` gives every test a private `HOME` and tmux
socket and clears all `HERMES_*` team variables). Plugin behaviour is tested against
`tests/py/plugin_fake.py`, a minimal fake Hermes plugin context — no real Hermes is needed.

```bash
pytest tests/py -q --cov=hermes_crew --cov-fail-under=90
ruff check . && ruff format --check .
mypy --strict plugin/hermes-crew/hermes_crew
shellcheck -s bash -x bin/* install.sh tests/run.sh tests/fake-hermes
```

New behaviour comes with a test. Bug fixes come with a test that fails without the fix.

## Pull requests

- One logical change per PR; keep it small enough to review.
- Branch from `main`; target `main`.
- Commit messages follow Conventional Commits, as in the history: `feat:`, `fix:`, `docs:`,
  `test:`, `chore:`, `ci:`, `refactor:` (imperative, lower-case summary, e.g.
  `fix: installer restores shared config link`).
- User-visible changes get a line under `## [Unreleased]` in `CHANGELOG.md`
  ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).
- Behaviour, commands or environment variables that change must be reflected in `README.md` / `docs/`.
- Versions are changed only with `scripts/bump-version.sh <x.y.z>`; `scripts/check-version.sh`
  verifies that `__version__`, `plugin.yaml`, both skills and the git tag agree.
- The terminal guard is best-effort and documented as such; do not present it as a security boundary.
- Fill in the PR template and make sure CI is green.

## Reporting bugs and ideas

Use the issue templates. For bugs, include the output of `hermes-crew doctor`, your Hermes
version and the relevant lines of `hermes-crew log` (from `.team/` — remove anything private).
Security issues: please do not open a public issue; contact the maintainer directly.

## License

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
