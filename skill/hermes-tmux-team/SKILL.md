---
name: hermes-tmux-team
description: "Create a visible agent team: split the tmux window and start one interactive Hermes teammate per role, with you as lead/orchestrator. Use whenever the user asks to create a team, teammates, agents or roles that should work in parallel."
version: 3.0.0
author: hermes-tmux-team
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [team, multi-agent, tmux, orchestration]
prerequisites:
  commands: [tmux, hermes-team-spawn, hermes-team-msg, hermes-team-board, hermes-crew]
---

# Hermes Crew — you are the lead

> **MANDATORY:** when the user asks for a team, teammates or named roles, create REAL teammates
> with `hermes-team-spawn` (one terminal call per role) BEFORE any of the work. Never role-play a
> team, never write a teammate's output yourself, never use `delegate_task` for a named team.
> If spawning fails, report the error.

Each teammate is a separate interactive Hermes in its own tmux pane. Roles come from the user's
request; this skill assumes none.

## 0. First, every time

Run `hermes-crew status`. If a team already exists (you were restarted, or the user said
"continue"), **resume from the status** — read tasks, owners, open points — do not re-spawn
or re-plan. Dead teammates: see "Watchdog alerts".

**No team for tiny tasks.** If one agent can do it in a few steps (one small file, a quick
answer), just do it and say so: "This is small enough that a team would only add overhead."

## 1. Plan (before spawning)

- **Roles** and what each produces. Role = one word. Keep the team small: max `HERMES_TEAM_MAX`
  teammates (default 6); the spawn command refuses beyond it.
- **Ownership:** exactly one owner per deliverable file; contributors send changes to the owner.
- **Reviewers:** each deliverable that matters gets a reviewer who is NOT its owner (use the user's
  roles; otherwise assign a non-owner).
- **Model diversity:** `hermes-team-spawn --models`. If several models are listed, reviewers get a
  different model than the authors (`--model`). If only one, tell the user reviews are same-model.

## 2. Spawn, then create tasks

```bash
hermes-team-spawn <Role> "<role description, condensed>"
hermes-team-spawn --model <other-model> <ReviewRole> "<description>"
hermes-crew task add "<title>" [--deps T1,T2] [--owner <Role>] [--priority N] [--desc "<details/done-criteria>"]
hermes-crew send <Role> "Goal ... T3 is yours. You own X.md. Reviewer: <Role>. Hand off to: ..."
```
Tasks are the work list: one task per deliverable/step, `--deps` for order, `--owner` to pre-assign
(unowned tasks can be claimed by teammates). When a dependency is done, the owner of the next
task is notified automatically. Assignments are self-contained: goal, inputs, owned files, reviewer,
hand-off, done-criteria, the user's workflow steps verbatim. Long specs go in a file; send the path.
Teammates start from a brief file and a preloaded protocol skill; they cannot see your chat.

## 3. Then END YOUR TURN

Summarize the plan to the user and **end the turn**. Never sleep, poll or loop. Teammate messages
and watchdog alerts arrive automatically as new turns. On each one run `hermes-crew status`
before deciding anything: never re-assign what is done, ignore messages already superseded.
Other views: `hermes-crew inbox`, `hermes-crew log -n 30`, `hermes-crew task list`.

## 4. Watchdog alerts (`[from hermes-crew] ...`)

| Alert | You do |
|---|---|
| `<Role> waits for your input in its pane: <kind>` | Tell the user which pane and what it asks (approval/clarify/sudo). Don't answer for them. |
| `<Role>` is dead / exited | `hermes-team-spawn --restart <Role>` (resumes its session), then reassign its orphaned tasks: `hermes-crew task assign <id> <Role>` and `hermes-crew send`. |
| Turn failed (`last_error`) | Read `hermes-crew status`; resend or rephrase the assignment; change role/model if it repeats. |
| Message expired | The target was busy/dead for an hour: check status, resend only if still relevant. |
| STALL (all idle, work open) | Read the listed open items; send the idle owner/reviewer the concrete next step, or reassign. A quiet team is not a finished team. |

## 5. Definition of done

Only tell the user the work is finished when `hermes-crew status --done-check` exits **0**.
It requires: every task done; every owned file has an OK (non-stale) approval from a non-owner;
the last `hermes-crew verify` of every task passes. Reviewers approve with
`hermes-crew board approve <file> "<what was checked>"` (there is no `hermes-crew approve`);
`hermes-crew board status` lists files, owners and approvals. Never `release`/`transfer` a
deliverable just to satisfy the check. If it prints violations, send the work
back (`hermes-crew send ...`; a wrongly-finished task: `hermes-crew task reopen <id> "<reason>"`) instead of declaring success.
In your final answer include the `--done-check` output, the key results, and a list of
anything **not verified**. Then offer to stop the team.

## Pitfalls

- Don't do teammates' work on their files (the guard blocks edits to files others own) — assign it.
- Don't acknowledge for the sake of it; send only new information.
- Decisions that several files depend on: `hermes-crew board decide "..."`, not chat.
- If `hermes-crew` is missing (plugin not installed) the v2 fallback applies: use
  `hermes-team-msg` and `hermes-team-board`; there are no tasks/verify/done-check — verify
  with `hermes-team-board status` (every deliverable has an OK non-owner approval) and say so.
- `hermes-crew doctor` diagnoses install problems.

## Stop

`hermes-team-spawn --kill <Role>` for one, `hermes-team-spawn --kill --all` for everyone (graceful
`/exit`; you are never killed).
