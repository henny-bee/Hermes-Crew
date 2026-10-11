Create a team with 2 teammates and you as the orchestrator.

1. Engineer: Read the project, then write README.md: what wordfreq does, how to run it (with the real
   options from `python wordfreq.py --help`), and one example with its real output. Run the example
   through `hermes-crew verify` so the output is recorded, then ask the Reviewer for a review.

2. Reviewer: Check README.md against the real behaviour of wordfreq.py (run it yourself). Approve the file
   with `hermes-crew board approve`, or send the Engineer a short list of fixes.

Goal: this project has no README.md. Add one that explains what the project does, how to run it, and shows
one example.

Create the work as tasks (Engineer, then Reviewer depending on it). You are done only when
`hermes-crew status --done-check` passes; then give me a 3-line summary of what changed and what was verified.
Keep it small: do not change wordfreq.py.
