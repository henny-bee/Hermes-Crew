---
name: hermes-tmux-team
description: "Create a visible agent team: split the tmux window and start one interactive Hermes teammate per role, with you as lead/orchestrator. Use whenever the user asks to create a team, teammates, agents or roles that should work in parallel."
version: 2.0.0
author: hermes-tmux-team
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [team, multi-agent, tmux, orchestration]
prerequisites:
  commands: [tmux, hermes-team-spawn, hermes-team-msg, hermes-team-board]
---

# Hermes tmux team (Claude Code style agent teams)

> **MANDATORY:** When the user asks for a team, teammates or named roles, you MUST create
> real teammates with `hermes-team-spawn` (one terminal call per role) BEFORE doing any of
> the work. Never write the teammates' output yourself (no role-play / simulated team) and
> never use `delegate_task` for a named team. If spawning fails, report the error instead.

You are the **lead** (orchestrator). Each teammate is a separate interactive Hermes process
in its own tmux pane. The roles come from the user's request — this skill does not assume
any particular roles. What it adds is structure that keeps any team honest:

| Mechanism | Tool |
|---|---|
| Shared decisions (names, interfaces, formats, numbers) | `hermes-team-board decide "..."` / `decide --replaces <#> "..."` |
| One owner per file; others request changes from the owner | `hermes-team-board own <file>` |
| Hand a file over / give it up (owner or lead) | `hermes-team-board transfer <file> <Role>` / `release <file>` |
| Approvals bound to file content (expire when the file changes) | `hermes-team-board approve <file> "<what was checked>"` |
| Ground truth for you | `hermes-team-board status` |
| Messages (queued until the receiver is idle, timestamped) | `hermes-team-msg <Role> "..."` |

## 1. Plan the team (before spawning)

From the user's request, write down for yourself:
- **Roles** and what each produces.
- **Ownership:** exactly one owner per deliverable file. Co-authored documents still get one
  owner; contributors send content/changes to that owner.
- **Review relations:** every deliverable that matters gets at least one reviewer who is
  **not its owner**. Use the roles the user defined; if the user defined no reviewer for some
  deliverable, assign a non-owner teammate as its reviewer.
- **Model diversity:** run `hermes-team-spawn --models`. If more than one model is listed,
  give reviewing roles a **different model** than the authors of what they review
  (same-model reviewers share the author's blind spots and tend to agree). If only one model
  exists, tell the user that reviews come from the same model.

## 2. Spawn (one terminal call each)

```bash
hermes-team-spawn <Role> "<role description from the user, condensed>"
hermes-team-spawn --model <other-model> <ReviewRole> "<description>"
```
Role = one word. Each teammate gets the team protocol automatically, starts in ~20s and
replies "ready". The team directory is your current folder (`.team/board.log` lives there).

## 3. Assign work

Send each teammate a self-contained assignment: goal, input files, the files it **owns**,
who reviews its files, who it hands off to, and done-criteria. Include the user's workflow
steps verbatim where relevant.

```bash
hermes-team-msg <Role> "Goal ... You own X.md. Reviewer: <Role>. When done: ..."
```

## 4. Wait — end your turn

Reports arrive in your prompt as `[from <Role> sent HH:MM:SS] ...` when you are idle. After
assigning, summarize the plan to the user and **end your turn**. Do NOT sleep or poll. On each
report, check `hermes-team-board status` before deciding the next step — never re-assign work
the board or the files show as done, and ignore messages superseded by later ones.

## 5. Definition of done (any team)

Before telling the user the work is finished, run `hermes-team-board status` and confirm:
1. every deliverable exists and has an owner,
2. every deliverable has an **OK** approval from a non-owner (no `STALE`, no `!!`),
3. the deliverables follow the recorded decisions.

If something is STALE or unapproved, send it back for review instead of declaring success.
In your final answer include the status output (or its summary) and list anything not
verified. Then offer to stop the team: `hermes-team-spawn --kill --all`.

## Pitfalls

- Don't do teammates' work yourself on the same files — assign it.
- Long specs go into a file; send the path, not the text.
- A teammate stuck on an approval prompt needs the user: say which pane.
- A quiet team is not a finished team — check the board.
