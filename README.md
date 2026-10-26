# harness-runtime-session-export

Export the conversation of a DigitalOcean Harness Runtime session to a Markdown file.
Use the file to continue the conversation in a new session.

To move a session to a new session with one command, refer to [Move a session to a new session](#move-a-session-to-a-new-session).

## What it does

`export_transcript.py` is one Python script. You run it inside the old session with `doctl`.

The script does these steps:

1. It finds the coding agent that runs in the session.
2. It reads the conversation history of that agent.
3. It writes the conversation to `/workspace/.migrated-history/conversation.md`.
4. It prints one JSON line with the result.

The script only reads the files of the agent. It writes one output file and changes nothing else.
The workspace user owns the output file.

Supported agents:

- `claude-code`
- `codex`
- `opencode`
- `openhands`

## Requirements

- [`doctl`](https://docs.digitalocean.com/reference/doctl/how-to/install/), with the `harness-runtime` commands.
- A DigitalOcean API token. Refer to [Authentication](#authentication).
- The old session must be running.
- `curl` on your local computer, if you run the script from this repository.

The session image supplies `python3`. You do not need Python on your local computer.

## Install

You do not install the script. You send it to the session with `doctl harness-runtime exec`.

To use a local copy, clone this repository:

```sh
git clone https://github.com/digitalocean/harness-runtime-session-export.git
cd harness-runtime-session-export
```

## Authentication

The script uses the `doctl` login. It does not read a token itself.

1. Create a personal access token in the [DigitalOcean Control Panel](https://cloud.digitalocean.com/account/api/tokens).
2. Give the token access to create, read, and delete your Managed Agents sessions. A full-access token also works.
3. Log in with `doctl`:

```sh
doctl auth init
```

For more information, refer to [How to Create a Personal Access Token](https://docs.digitalocean.com/reference/api/create-personal-access-token/).

## Usage

Replace `OLD_SESSION_ID` with the ID or the unique name of the old session.

Run the script from this repository:

```sh
doctl harness-runtime exec OLD_SESSION_ID -- python3 -c "$(curl -fsSL https://raw.githubusercontent.com/digitalocean/harness-runtime-session-export/main/export_transcript.py)"
```

Run the script from a local copy:

```sh
doctl harness-runtime exec OLD_SESSION_ID -- python3 -c "$(cat export_transcript.py)"
```

The script finds the agent automatically. If it cannot find the agent, give the agent name as the last argument:

```sh
doctl harness-runtime exec OLD_SESSION_ID -- python3 -c "$(cat export_transcript.py)" codex
```

For a long conversation, increase the command timeout with `--timeout`. The value is in seconds:

```sh
doctl harness-runtime exec OLD_SESSION_ID --timeout 300 -- python3 -c "$(cat export_transcript.py)"
```

## Output

On success, the script prints one JSON line and exits with code 0:

```json
{"adapter": "codex", "source": "<path of the agent history>", "messages": 12, "path": "/workspace/.migrated-history/conversation.md"}
```

| Field | Description |
| --- | --- |
| `adapter` | The agent that the script exported. |
| `source` | The history file or session that the script read. |
| `messages` | The number of messages in the export. |
| `path` | The path of the Markdown file in the session. |

On failure, the script prints one JSON line with an `error` field and exits with code 1:

```json
{"error": "no codex rollout found"}
```

The Markdown file has this format:

```markdown
# Previous conversation

Exported from an earlier codex session. ...

## User

<message text>

## Assistant

<message text>
```

## Move a session to a new session

`migrate_session.py` moves one session to a new session with one command. It runs on your local computer and needs Python 3.8 or later. Clone this repository, then run the script with the ID or the unique name of the old session:

```sh
python3 migrate_session.py OLD_SESSION_ID
```

If you do not give the ID, the script asks for it.

The script does these steps:

1. It checks the old session. If the session is paused, the script asks to resume it.
2. It runs `export_transcript.py` in the old session.
3. It downloads the workspace of the old session.
4. It creates a new session from the Agent Config of the old session. The new session has the same agent, settings and secrets, and uses the current session image. The new session is named `<old name>-migrated-<month><day><hour><minute>`. To set a different name, use `--name`.
5. It uploads the workspace into the new session.
6. It tells the new agent to read the conversation, and it prints the reply of the agent. The script approves the requests of the agent to use tools during this message. To answer the requests yourself, use `--no-approve`.
7. It asks if it can remove the old session. The default answer is no. If the input is not a terminal, the answer is always no. To skip the question, use `--keep-old`.

You need one free session slot for the new session. Paused sessions count toward your session limit.

The script writes a log and its working files to the folder `migrate-OLD_SESSION_ID` in the current directory. The file `workspace.tar` in that folder is a copy of your workspace. Remove the folder when you do not need it.

If a step fails, the script tells you the cause. Fix the cause and run the same command again. The script does not repeat the steps that it finished.

To use a different `doctl` login, use `--context`. To use a different `doctl` program, use `--doctl` or set the `DOCTL` environment variable. For all options, run `python3 migrate_session.py --help`.

### Move a session manually

Do these steps to move a session without `migrate_session.py`. Use the same agent in the new session.

1. Export the conversation in the old session:

    ```sh
    doctl harness-runtime exec OLD_SESSION_ID -- python3 -c "$(curl -fsSL https://raw.githubusercontent.com/digitalocean/harness-runtime-session-export/main/export_transcript.py)"
    ```

2. Download the workspace of the old session:

    ```sh
    doctl harness-runtime download OLD_SESSION_ID --workspace-path /workspace --archive --save-to workspace.tar
    ```

3. Create a new session with the same agent and the same settings. Wait until the session is `READY`.

4. Upload the workspace into the new session:

    ```sh
    doctl harness-runtime upload NEW_SESSION_ID --local-file workspace.tar --workspace-path .mars-migrate --archive
    ```

5. Move the files to the root of the workspace:

    ```sh
    doctl harness-runtime exec NEW_SESSION_ID -- sh -c 'cp -a /workspace/.mars-migrate/workspace/. /workspace/ && rm -rf /workspace/.mars-migrate'
    ```

6. Send this text as your first message to the new agent. Replace the last part with your next request:

    ```text
    You may use tools now. The file /workspace/.migrated-history/conversation.md is our previous conversation, from another session. Read it and treat it as the earlier part of this conversation. Then continue with: <your next request>
    ```

7. If the agent asks for approval to read the file, approve it.

8. Make sure that the new session works. Then remove the old session:

    ```sh
    doctl harness-runtime remove OLD_SESSION_ID
    ```

## What carries over

These items carry over:

- All files in `/workspace`, with the same content and owners.
- The text of each user message and each agent message.
- The name of each tool call.

## What it does not do

- It does not copy tool inputs or tool outputs. If the agent needs an earlier result, tell it to run the command again.
- It does not copy the reasoning of the agent.
- It does not copy pending approvals.
- It does not copy software that you installed outside `/workspace`, or environment variables and secrets that you set in the old session after you created it. Set these up again in the new session. `migrate_session.py` creates the new session from the same Agent Config, so the settings and secrets of the Agent Config carry over.
- It does not move a conversation to a different agent. Use the same agent in the new session.
- It does not restore the history into the agent automatically. The new agent reads the Markdown file when you tell it to. `migrate_session.py` sends that message for you.
- `export_transcript.py` does not create, upload, or remove sessions. `migrate_session.py` does these steps with `doctl`.

## Contributing, security and license

- To contribute, refer to [CONTRIBUTING.md](CONTRIBUTING.md).
- To report a security problem, refer to [SECURITY.md](SECURITY.md).
- This project is licensed under the [Apache License, Version 2.0](LICENSE).
