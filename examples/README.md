# Worked examples

Three small tasks, each done by a **real Hermes team** (a lead plus an Engineer and a Reviewer, one interactive
Hermes per pane) and recorded by Hermes Crew itself. Nothing here is hand-written: the `.team/` directories, the
`status.json` snapshots and the diffs are what the run produced. Only machine-specific paths were scrubbed
(`/tmp/example`, `/home/user`) and the secrets check found nothing.

| Example | Task | Result | Done-check reached after | Tokens in / out (lead + Engineer + Reviewer) |
|---|---|---|---|---|
| [01-add-readme](01-add-readme/) | write a README for a 30-line CLI | `README.md`, approved by the Reviewer | 82 s | 842 k / 2.9 k |
| [02-add-tests](02-add-tests/) | unit tests for a small module | `tests/test_slugify.py` (11 tests), approved | 61 s | 649 k / 3.0 k |
| [03-config-migration](03-config-migration/) | move a config from `.ini` to `.json` | 4 files, approved one by one | 142 s | 1 360 k / 6.7 k |

Each example directory holds:

| File | What it is |
|---|---|
| `PROMPT.md` | the prompt the lead was started with |
| `project.diff`, `project.diffstat` | what the team changed in the project (the seed is in [.harness/seed](.harness/seed/)) |
| `.team/` | the complete team state: `board.log`, `tasks.jsonl`, `verify.jsonl`, `events.jsonl`, `handoff/`, `agents/`, `briefs/`, `mail/` |
| `status.json`, `status.txt`, `done-check.txt` | `hermes-crew status` as it was when the run ended (`metrics` = cost and timing) |
| `panes/` | the final screen of each Hermes pane |
| `progress.log` | the monitor's 20-second status samples |
| `meta.txt` | Hermes and hermes-crew versions, model, settings, timebox and the exact command |

## What these runs do and do not show

- **One run each, tiny tasks.** They show the machinery working end to end: tasks with a dependency, file
  ownership, `hermes-crew verify` evidence, approval by another role, `status --done-check` turning green, and
  messages delivered between panes. They are not a benchmark.
- **Nothing stalled, nothing was reopened.** No task was blocked or reopened, and no approval prompt appeared.
  The watchdog had nothing to report. Harder tasks will produce harder stories.
- **A review is not a proof.** Example 02's module has a boundary bug on purpose (`slugify("hello world", 6)` returns
  `"hello-"`, a trailing dash). The Engineer's tests used `max_len` values that avoid it, the Reviewer approved, and
  the lead reported "nothing left unverified". Verified here means what the recorded commands checked.
- **`board.unparsed` events** in examples 01 and 03 are the guard saying that it allowed a terminal command it
  could not classify (a `python3 -c` script). See [architecture](../docs/architecture.md#allow-by-uncertainty-boardunparsed).
- **Setup:** Hermes 0.21.5, model `claude-fable-5-1` at low reasoning effort through a local proxy, 40 turns per
  turn budget, memory and MCP servers off. The code under test was the working tree of this repository on top of
  commit `54cb978` (not a tagged release).
- **Token numbers** are what the plugin counted from Hermes' usage records; they include cached prompt tokens (see
  the known gaps in [hermes-api-contract.md](../docs/hermes-api-contract.md)). `metrics.tasks.*.wall_s` counts from
  the moment a task got an owner, so a task that waits for its dependency (T2 in every example) includes that wait.
- **Removed from the copies:** `.team/.gitignore` (Hermes Crew writes `*` into it so team state never lands in a
  user's repo; it would hide these files from git), the empty lock files (`board.log.lock`, `locks/`).
- **Harness directory renamed:** the runs were started from `examples/_harness/`, which was renamed to `examples/.harness/` afterwards
  (a hidden directory, so the check `for d in examples/*/` only sees example directories). The command line in each `meta.txt` was
  updated to the new path. Example 01 was run from a scratch copy of the same script (absolute paths for the repo); 02 and 03
  were run from `examples/_harness/` itself.

To reproduce a run, see [.harness/README.md](.harness/README.md).
