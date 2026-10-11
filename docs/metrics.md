# Metrics

`hermes-crew status --json` has a `metrics` object. It is computed on demand from
`.team/tasks.jsonl` and `.team/agents/<Role>.json`; nothing new is stored.

## `metrics.tasks.<id>`

One entry per task id in `tasks.jsonl`, ordered T1, T2, … T10.

| Field | Meaning |
|---|---|
| `claimed` | Local ISO time (`YYYY-MM-DDTHH:MM:SS`) when the task first got an owner: `task claim`, `task assign` or `task add --owner`. `null` if it never had one. Later claims (e.g. after an orphan) do not move it. |
| `done` | Local ISO time of the last `task done`. `null` if the task is not done, or was reopened after its last done. |
| `wall_s` | Seconds from `claimed` to `done` (one decimal), so rework after a reopen is included. `null` unless both exist. |
| `reopens` | How many times `task reopen` was run on the task. |

## `metrics.roles.<Role>`

One entry per agent record, plus roles known to the team without a record (with 0).

| Field | Meaning |
|---|---|
| `tokens_in`, `tokens_out` | Tokens counted by the plugin from Hermes' `post_api_request` usage (prompt/input and completion/output). Cumulative for the role in this team dir. Roles that run without the plugin show 0. |

## `metrics.totals`

| Field | Meaning |
|---|---|
| `tasks_timed` | Number of tasks with a non-null `wall_s`. |
| `wall_s` | Sum of those `wall_s` values — the sum of task durations, **not** elapsed team time: parallel tasks overlap. |
| `reopens` | Sum of `reopens` over all tasks. |
| `tokens_in`, `tokens_out` | Sum over all roles. |

## Example

```json
{"tasks": {"T1": {"claimed": "2026-10-11T09:00:00", "done": "2026-10-11T09:01:40", "wall_s": 100.0, "reopens": 0}},
 "roles": {"Eng": {"tokens_in": 200, "tokens_out": 40}},
 "totals": {"tasks_timed": 1, "wall_s": 100.0, "reopens": 0, "tokens_in": 200, "tokens_out": 40}}
```

Real numbers from three team runs: [what a team costs](architecture.md#what-a-team-costs). See [commands](commands.md) for `status`, and [architecture](architecture.md) for how `.team/` is laid out.
