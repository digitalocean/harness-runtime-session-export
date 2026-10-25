# Contributing

Thank you for your interest in this repository. It holds `export_transcript.py`, a script that exports the conversation of a DigitalOcean Harness Runtime session to a Markdown file.

By contributing, you agree that your contribution is licensed under the [Apache License, Version 2.0](LICENSE).

To report a security vulnerability, follow [SECURITY.md](SECURITY.md). Do not open an issue.

## Set up

1. Fork the repository and clone your fork.
2. Install `doctl` and authenticate it. The [README](README.md) gives the steps.
3. Create a test session that you can remove after the test. Do not test on a session that you need.

## Test a change

The script uses only the Python standard library. It runs inside the session, not on your local computer.

1. Check the syntax on your local computer:

   ```sh
   python3 -m py_compile export_transcript.py
   ```

2. Run your copy of the script in the test session:

   ```sh
   doctl harness-runtime exec TEST_SESSION_ID -- python3 -c "$(cat export_transcript.py)"
   ```

3. Read the output file and make sure that the conversation is complete:

   ```sh
   doctl harness-runtime exec TEST_SESSION_ID -- cat /workspace/.migrated-history/conversation.md
   ```

4. Do steps 2 and 3 for each agent that your change affects.

Never commit keys, tokens, session IDs or conversation content.

## Open a pull request

1. Create a branch from `main` in your fork.
2. Keep the change focused on one topic.
3. Update the README if the behavior, the output format or a command changes.
4. Open a pull request against `main`. Tell what changed, why, and which agents you tested.
