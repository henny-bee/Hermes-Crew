# Lessons: 03-config-migration

One run, the largest of the three (move a config from `.ini` to `.json`, four files), lead + Engineer +
Reviewer. Done-check was green after 142 s. Nothing stalled, nothing was blocked or reopened, no watchdog
alert. A few details are worth recording anyway.

## What happened

| Time | Event |
|---|---|
| 02:33:35 / :37 | lead adds T1 (Engineer) and T2 (Reviewer, depends on T1) |
| 02:33:55 | Engineer owns `app.py`, `migrate_config.py`, `tests/test_migration.py` |
| 02:34:36 | Engineer owns `config.json` — **after** it had created it by running the script |
| 02:34:46 | T1 done (6 tests, verified twice with identical output) |
| 02:35:25–:31 | Reviewer approves the four files one by one |
| 02:35:35 | T2 done; done-check green at 02:35:39 |

## Lessons

1. **`done-check` was red exactly when it should be.** The Reviewer ran `hermes-crew status --done-check`
   three times: exit 1 before approving, exit 1 after approving all four files but before `task done T2`, and
   exit 0 after. The gate counts open tasks, not just approvals.
2. **A file written by a script is invisible to the ownership guard.** `config.json` was produced by running
   `migrate_config.py`, not by `write_file`, so nothing claimed it automatically (the guard only checks
   recognised writes; see [Known limitations](../../docs/architecture.md#known-limitations)). The Engineer
   claimed it explicitly afterwards. Had it not, `config.json` would not have appeared among the owned
   files and nobody would have been required to approve it.
3. **Idempotence was tested, twice, the easy way.** `migrate_config.py` refused to overwrite an existing
   `config.json` (exit 1) both when the Engineer and when the Reviewer ran it on the real `config.ini`;
   the Reviewer then migrated into a temp file and compared. That is the requirement "must not overwrite"
   behaving as specified.
4. **The parity check covered the happy path only.** The Reviewer compared old (`git show HEAD:app.py`) and new
   `app.py` output on the real config and recorded it with `hermes-crew verify` (`PARITY CONFIRMED`). The new
   `load_config` also swallows *any* error and returns the defaults (`except Exception`), so a corrupt
   `config.json` now silently turns into defaults, where the old `configparser` code raised on a malformed
   file. The 6 tests cover missing file, missing keys and no-overwrite, not malformed JSON. Nobody asked
   about that and no log mentions it; approval means "matches on the cases that were run".
5. **The lead gave an instruction that does not exist.** Its long `hermes-crew send` to the Engineer told it
   to run `hermes-crew board claim …`; the real subcommand is `board own`. It did no harm because the message
   was delivered at 02:34:54, after T1 was done (see the message-timing lesson in
   [01](../01-add-readme/LESSONS.md)); the Engineer had already used `board own`.
6. **`board.unparsed` fired twice:** the lead's `hermes-crew send` (a false positive: it writes no project
   file, but its long multi-line text matched the "looks like a write" patterns) and the Reviewer's
   `python3 -c` parity script, which was then re-run through `verify` to get an evidence record.
7. **The watchdog's silence says little:** its prompt-wait threshold is 3 minutes; the run lasted 142 s.

## Cost

1.36 M input / 6.7 k output tokens (input includes cached prompt tokens). T1 71 s; T2 shows 118 s, but about
70 s of that is waiting for T1 — the Reviewer's own work ran 02:34:47–02:35:35 (48 s). See
[docs/metrics.md](../../docs/metrics.md).
