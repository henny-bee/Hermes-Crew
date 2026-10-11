Create a team with 2 teammates and you as the orchestrator.

1. Engineer: Write unit tests for slugify.py in tests/test_slugify.py using the standard library `unittest`
   only (no pip installs). Cover slugify (normal text, punctuation, max_len) and parse_duration (valid and
   invalid input). Run `python -m unittest discover -s tests -v` through `hermes-crew verify` so the result is
   recorded. If a test shows a real bug in slugify.py, do NOT edit slugify.py: tell the lead what you found.

2. Reviewer: Read tests/test_slugify.py and slugify.py, run the tests yourself, and decide whether the tests
   are meaningful (they must assert behaviour, not just call the functions). Approve the test file with
   `hermes-crew board approve`, or send the Engineer a short list of fixes.

Goal: slugify.py has no tests. Add a test suite for it.

Create the work as tasks (Engineer, then Reviewer depending on it). If a real bug is found, the lead decides
how it is handled (a new task for the right owner). You are done only when `hermes-crew status --done-check`
passes; then give me a 3-line summary of what changed and what was verified.
