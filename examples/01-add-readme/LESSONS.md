# Lessons: 01-add-readme

One run, tiny task (README for a 30-line CLI), lead + Engineer + Reviewer. Done-check was green after
82 s. **Nothing stalled, nothing was blocked, nothing was reopened, and the watchdog never raised an
alert.** This file only records what the logs actually show; there is no drama to report.

## What happened

| Time | Event (source: `.team/tasks.jsonl`, `board.log`, `events.jsonl`) |
|---|---|
| 02:28:19 / :21 | lead adds T1 (Engineer) and T2 (Reviewer, depends on T1) |
| 02:28:28 | Engineer starts T1 |
| 02:28:38 | Engineer owns `README.md` |
| 02:28:56 | T1 done (2 `verify` records: the file examples) |
| 02:29:12 | Reviewer approves `README.md` |
| 02:29:14 | T2 done; done-check green at 02:29:25 |

## Lessons

1. **Teammates did not wait for the lead's message; they read the task list.** Their brief says
   "wait for your assignment", but the Engineer ran `hermes-crew status` and `task show T1` within seconds
   and started T1 itself. The lead's detailed instruction message was only delivered to the Engineer at
   02:29:02, six seconds *after* T1 was done: mail is injected only while an agent is idle, and the
   Engineer was busy the whole time. The same happened to the Engineer's "ready for review" message to the
   Reviewer (queued 02:28:57, delivered 02:29:18, after T2 was done). What a lead writes into the **task
   description** is what a busy teammate sees; a follow-up message may arrive too late to matter.
2. **Evidence is only as good as what went through `verify`.** The Engineer checked the README's stdin
   example with a direct `python3 -c` command, which is not a recorded verify. Only the Reviewer's later
   `hermes-crew verify T2 -- python3 -c …` put that example on record. Both commands also appear as
   `board.unparsed` events (see below).
3. **`board.unparsed` fired twice**, both times for a `python3 -c` script. The guard allowed them because it
   cannot see what an interpreter writes; here nothing was written. That is the feature working as
   intended: the blind spot is visible in `hermes-crew log --kind board.unparsed`.
4. **Teammates guess CLI syntax.** The Engineer's first `hermes-crew board` (no subcommand) exited 2 and
   cost one call before it ran `board status` and `board own README.md`. Cheap, but it shows the usage
   text and skills matter.
5. **The review was real work, but short.** The Reviewer ran `-h`, both README examples and the stdin case
   (6 recorded verifies in total, 4 of them the Reviewer's) before approving; the `README.md` approval note lists
   what was checked.
6. **The watchdog's silence says little here.** It alerts after a teammate waits more than 3 minutes on a
   prompt; the whole run lasted 82 s, so a stall of that kind could not even have happened.

## Cost

842 k input / 2.9 k output tokens across the three agents (input includes cached prompt tokens), task
wall-clock T1 36 s, T2 52 s (T2's clock started when the lead added it, so it includes waiting for T1).
See [docs/metrics.md](../../docs/metrics.md) and [docs/architecture.md](../../docs/architecture.md#what-a-team-costs).
