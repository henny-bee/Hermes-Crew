---
name: hermes-tmux-team
description: "Create a visible agent team: split the tmux window and start one interactive Hermes teammate per role (planner, engineer, reviewer, ...), with you as lead/orchestrator. Use whenever the user asks to create a team, teammates, agents or roles that should work in parallel."
version: 1.0.0
author: hermes-tmux-team
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [team, multi-agent, tmux, orchestration]
prerequisites:
  commands: [tmux, hermes-team-spawn, hermes-team-msg]
---

# Hermes tmux team (Claude Code style agent teams)

You are the **lead** (orchestrator). Each teammate is a separate interactive Hermes
process in its own tmux pane, visible to the user. Use this instead of `delegate_task`
whenever the user asks for a team / teammates / named agents / roles.

## 1. Spawn teammates (one terminal call each)

```bash
hermes-team-spawn Planner "Breaks the goal into concrete steps and acceptance criteria."
hermes-team-spawn Engineer "Implements the plan: edits files, runs commands and tests."
hermes-team-spawn Supervisor "Reviews the Engineer's work against the plan; approves or requests changes."
```

- Role = one word (it becomes the pane label and the address for messages).
- Description = the role text the user gave you, condensed.
- If the user also asks for an "orchestrator"/"lead", that is **you** — do not spawn one.
- If the command says Hermes is not inside tmux, tell the user to start Hermes with
  `hermes-wsl` (Windows) or `hermes-tmux` (Linux/WSL) and stop.
- Each teammate needs ~20s to start, then replies "ready".

## 2. Assign work

```bash
hermes-team-msg Planner "Goal: <goal>. Produce a step plan, then send it to Engineer and report to lead."
hermes-team-msg --list          # show roles and panes
```

Messages are queued and typed into the teammate's prompt only when it is idle, so
they never interrupt. Give each teammate a self-contained task (goal, files, done-criteria).
Tell teammates explicitly who to hand off to (e.g. Planner → Engineer → Supervisor → lead).

## 3. Wait for reports — end your turn

Teammates report with `hermes-team-msg lead "..."`. Their reports appear as new user
messages in **your** prompt, prefixed `[from <Role>]`, delivered when you are idle.
So after assigning work: summarize the plan for the user and **end your turn**.
Do NOT sleep, poll, or loop waiting. When a `[from ...]` message arrives, react to it
(next assignment, fix request, or final summary to the user).

## 4. Finish

When the work is done, give the user a summary, then stop the team if they agree:

```bash
hermes-team-spawn --kill --all      # or: hermes-team-spawn --kill Engineer
```

## Pitfalls

- Never do teammates' work yourself in parallel on the same files — assign it.
- Keep messages short; put long specs in a file in the project and send the path.
- A teammate stuck on an approval prompt needs the user: say which pane.
