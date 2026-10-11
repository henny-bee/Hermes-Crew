# Public launch checklist

The artefacts for announcing Hermes Crew to the Hermes community. **Executing the launch is a human decision.**
Nothing on this page has been posted, pushed or changed on GitHub.

## 1. Before announcing (blocking)

| # | Item | State | How to close it |
|---|---|---|---|
| 1 | Commit the work and push a branch; let CI run on GitHub | not done: CI has only been run locally | `git push`, then watch `ci.yml` (all jobs) and `smoke.yml` |
| 2 | macOS: declare it only after the `macos-latest` jobs are green (plan T43) | blocked: never ran on a real macOS runner | if green: add `macos` to `plugin/hermes-crew/plugin.yaml`, both `skill/*/SKILL.md` and the README requirements table |
| 3 | Guard refactor acceptance (plan T50) | open by decision: the hand-written tokenizer is kept (a `shlex` tokenizer fails 2 existing tests and lets an unbalanced quote unblock writes), and `guard/` is ~445 lines against the 350 target | decide to accept the size or rework it; behaviour is unchanged (differential fuzz against the old `guard.py`: 0 differences) |
| 4 | Release version and tag | the local annotated tag `v3.0.0` points at the pre-plan commit `54cb978`; the new work is under `[Unreleased]` | cut it as **3.1.0** (new features: `lock`, version-aware `doctor`, degraded mode, `board.unparsed`, `metrics`): `bash scripts/bump-version.sh 3.1.0`, move `[Unreleased]` to `[3.1.0]` in `CHANGELOG.md`, commit, `git tag -a v3.1.0`, push the tag; `release.yml` builds the tarball and the GitHub Release from the changelog section |
| 5 | Optional demo (plan T22) | skipped | an asciinema/GIF recording of a real run on line 3 of the README |

## 2. Repository settings (GitHub CLI, run by a maintainer)

```bash
gh repo edit henny-bee/Hermes-Crew \
  --add-topic hermes-agent --add-topic multi-agent --add-topic tmux --add-topic agent-orchestration
gh repo edit henny-bee/Hermes-Crew --enable-discussions
gh repo view henny-bee/Hermes-Crew --json repositoryTopics,hasDiscussionsEnabled   # check
```

## 3. Announcement text

> **Hermes Crew: visible agent teams for Hermes Agent**
>
> Hermes Crew is an add-on that turns one Hermes into a team. Ask your Hermes to *"create a team with a Planner,
> an Engineer and a Supervisor"*. It splits your tmux window and starts one interactive Hermes per teammate,
> each in its own labelled pane.
>
> What you get over `delegate_task`:
> - **Visible, truly parallel sessions.** You can watch, talk to and interrupt every teammate.
> - **A shared task list** with dependencies and handoff notes.
> - **File ownership** enforced before writes, and approvals bound to file content.
> - **Evidence.** `hermes-crew verify` records the command and its exit code, and the lead may only say "done" when
>   `hermes-crew status --done-check` passes.
>
> It uses only Hermes' documented plugin and skill extension points; Hermes itself is not modified. Tested with
> Hermes 0.19.0 and 0.21.5, on Linux and Windows (WSL2).
>
> Three worked examples with the complete team logs (only machine paths scrubbed) are in
> [examples/](https://github.com/henny-bee/Hermes-Crew/tree/main/examples). One of them is an honest miss: the
> reviewer approved tests that did not catch a planted bug.
>
> Install: `git clone https://github.com/henny-bee/Hermes-Crew && cd Hermes-Crew && ./install.sh`. Feedback welcome
> in Discussions.

## 4. Facts the announcement relies on

- **Worked examples** ([examples/](../examples/README.md)). These are real three-agent Hermes teams (claude-fable-5-1, low
  reasoning effort):

  | Example | Done-check green after | Tokens in / out |
  |---|---|---|
  | 01-add-readme | 82 s | 842 k / 2.9 k |
  | 02-add-tests | 61 s | 649 k / 3.0 k |
  | 03-config-migration | 142 s | 1.36 M / 6.7 k |

- **Tested Hermes versions:** `compat.KNOWN`, rendered into the README by `scripts/gen-compat-row.py`.
- **Hermes integration surface:** [hermes-api-contract.md](hermes-api-contract.md), including its known gaps.
- **Quality gates in CI:** version check, ruff, mypy `--strict`, pytest with coverage ≥ 90 % on Python 3.10, 3.12
  and 3.13, shellcheck, `tests/run.sh`, and a real-Hermes smoke test.
