# Lessons: 02-add-tests

One run, tiny task (unit tests for `slugify.py`), lead + Engineer + Reviewer. Done-check was green after
61 s. Nothing stalled, nothing was blocked or reopened, no `board.unparsed` events, no watchdog alert.
**But the team missed the bug that was planted in the module, and everybody signed off.** That is the
lesson of this example.

## The miss

`slugify.py` truncates *after* stripping dashes: `slug[:max_len]` can leave a trailing dash, so
`slugify("hello world", 6)` returns `"hello-"`. The Engineer's `test_max_len_truncation` asserts
`max_len=5` (`"hello"`) and `max_len=7` (`"hello-w"`) — the two values that avoid the bug. The prompt even
said "If a test shows a real bug in slugify.py, do NOT edit slugify.py: tell the lead what you found.", but no test exercised the boundary, so
nothing could show one.

The record of the review (`board.log`, `verify.jsonl`):

- T2 ran from 02:31:45 to 02:31:52 — **seven seconds**.
- The recorded evidence for T2 is one command: the Reviewer re-ran the Engineer's own test suite
  (`python -m unittest discover -s tests -v`, 11 tests, exit 0). The Reviewer read `slugify.py` and the test file (see `panes/Reviewer.txt`) but ran no
  command of its own beyond that suite, so it did not probe `slugify` with inputs the tests do not cover.
- The approval note says "covering … boundary, formatting, truncation, and invalid input cases".
- The lead's final report: "Nothing left unverified: all test cases passed and all files received non-owner
  approval."

Every gate behaved as designed: tests passed, the file was approved by a non-owner, `status --done-check`
turned green. **Verified means what the recorded commands checked.** Passing tests prove the tests pass, not
that the module is right, and an approval from a reviewer who only re-runs the author's tests adds a second
pair of eyes but not a second test. If the goal is to find bugs, the Reviewer's task must say so
explicitly (for example: "write and run three inputs of your own that the tests do not cover, with `verify`").

## What else the logs show

- **Same message-timing effect as in 01:** the lead's detailed instruction reached the Engineer at 02:31:48,
  after T1 was done (02:31:42). The Engineer worked from the task description.
- The Engineer ran `python3 -m unittest discover` once before writing anything (exit 5: no tests found) and
  guessed a CLI subcommand that does not exist (`hermes-crew board files`, exit 2). Both are harmless.
- The watchdog could not have caught anything: its prompt-wait threshold is 3 minutes and the run took 61 s.

## Cost

649 k input / 3.0 k output tokens (input includes cached prompt tokens), T1 31 s, T2 39 s (includes the wait
for T1). See [docs/metrics.md](../../docs/metrics.md).
