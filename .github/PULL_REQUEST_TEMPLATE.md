## What and why

<!-- What does this change, and why? Link the issue if there is one. -->

## How it was tested

- [ ] `pytest tests/py -q`
- [ ] `bash tests/run.sh`
- [ ] `ruff check . && ruff format --check .` and `mypy --strict plugin/hermes-crew/hermes_crew` (if Python changed)
- [ ] `shellcheck` (if shell scripts changed)

## Checklist

- [ ] New behaviour or bug fix has a test
- [ ] `CHANGELOG.md` has a line under `## [Unreleased]` for user-visible changes
- [ ] `README.md` / `docs/` updated for changed commands, environment variables or behaviour
- [ ] Hermes' own code is not modified; only documented extension points are used
