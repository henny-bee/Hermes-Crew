Create a team with 2 teammates and you as the orchestrator.

1. Engineer: Migrate the app's configuration from config.ini to config.json.
   - Write migrate_config.py: reads config.ini and writes config.json with the keys host, port, db_url, debug
     (the same names load_config() returns today; port is an int, debug is a bool). It must not overwrite an
     existing config.json.
   - Change app.py so load_config() reads config.json (same defaults as before when keys are missing).
   - Write tests in tests/test_migration.py with the standard library `unittest` only (no pip installs).
   Run the tests through `hermes-crew verify` so the result is recorded, then ask the Reviewer for a review.

2. Reviewer: Run migrate_config.py on the real config.ini and check that app.py gives the same values as before
   the change (compare with `git stash`-free methods: read the old code in git history, do not modify files
   you do not own). Approve each file with `hermes-crew board approve`, or send the Engineer a short list of fixes.

Goal: the project reads config.ini today; after the change it reads config.json, and migrate_config.py converts
an existing config.ini.

Create the work as tasks (Engineer, then Reviewer depending on it). You are done only when
`hermes-crew status --done-check` passes; then give me a 3-line summary of what changed and what was verified.
