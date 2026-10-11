# Usage

Open one Hermes and ask it to *"create a team with a Planner, an Engineer and a Supervisor"*.
Hermes splits the terminal and starts one **interactive Hermes per teammate**, each in its own
labelled pane. They share a task list, message each other and report back to the lead.

```powershell
cd C:\path\to\your\project
hermes-wsl
```

(On Linux / in a WSL shell: `cd` into the project and run `hermes-tmux`.) Then paste a prompt like:

```
Create a team with 3 teammates and you as the orchestrator.

1. Planner: Read the project and break the goal into small, concrete tasks with clear
   done-criteria. Write the plan to PLAN.md, then hand it to the Engineer.

2. Engineer: Implement the tasks one by one. Edit the files, run the code/tests through
   `hermes-crew verify` so the result is recorded, then ask the Supervisor for a review.

3. Supervisor: Review the Engineer's real files against PLAN.md. Approve, or send the
   Engineer a short list of fixes. Report the final result to the lead.

Goal: Add a README.md to this project that explains what it does, how to install it,
and how to run it.

Create the work as tasks (Planner → Engineer → Supervisor, each depending on the one
before). You are done only when `hermes-crew status --done-check` passes; then give me
a short summary of what changed and what was verified.
```

Only change the **Goal** line for other jobs. Tiny jobs don't need a team — the lead will tell you
and just do them. If the lead is restarted, it resumes from `hermes-crew status` instead of
starting over. When done, type `stop the team`.

Watch the team from a second tmux window: `hermes-crew watch`.

## Controls (tmux)

| Action | Keys |
|---|---|
| Talk to a teammate | click its pane (mouse is on) or `Ctrl+b` + arrow |
| Zoom a pane / unzoom | `Ctrl+b` `z` |
| Leave, agents keep running | `Ctrl+b` `d` — run `hermes-wsl` in the same folder to re-attach |
| Kill everything | `wsl tmux kill-session -t hermes-<folder>-<hash>` (see `tmux ls`; the hash is of the full path) |
| Copy text with mouse on | hold `Shift` while selecting |

See also: [commands](commands.md), [environment variables](env.md), [troubleshooting](troubleshooting.md).
