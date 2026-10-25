# harness-runtime-session-export

Export the conversation of a DigitalOcean Harness Runtime session to a Markdown file.
Use the file to continue the conversation in a new session.

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

Do these steps to continue the conversation in a new session. Use the same agent in the new session.

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
- It does not copy environment variables, secrets, or software that you installed outside `/workspace`. Set these up again in the new session.
- It does not move a conversation to a different agent. Use the same agent in the new session.
- It does not restore the history into the agent automatically. The new agent reads the Markdown file when you tell it to.
- It does not create, upload, or remove sessions. You do these steps with `doctl`.

## Security

To report a security problem, refer to [SECURITY.md](SECURITY.md).
