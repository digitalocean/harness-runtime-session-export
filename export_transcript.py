"""Export a DigitalOcean agent session's conversation to a Markdown file.

Run it inside the session you are moving away from:

    doctl harness-runtime exec OLD_SESSION_ID -- python3 -c "$(cat export_transcript.py)"

It writes /workspace/.migrated-history/conversation.md, owned by the workspace
user. Download the workspace, upload it into a new session, and ask the new
agent to read that file. It only reads the agent's files; it changes nothing
except that one output file. Supports claude-code, codex, opencode and
openhands; the agent is detected from the session, or pass its name as the
only argument.

Prints one JSON line: {"adapter", "source", "messages", "path"}.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys

WORKSPACE = "/workspace"
OUT_DIR = os.path.join(WORKSPACE, ".migrated-history")
OUT = os.path.join(OUT_DIR, "conversation.md")
HOME_ROOTS = ["/var/lib/plano-agent", os.path.join(WORKSPACE, ".agent-home"), "/root", "/home", "/tmp"]
RECORD_DIRS = ["/var/lib/plano-agent", os.path.join(WORKSPACE, ".agent-home")]
OHR_CONFIGS = ["/etc/plano/config.override.yaml", "/etc/plano/config.yaml"]
RUNTIME_LINE = re.compile(r"^[\s-]*runtime:\s*[\"']?([A-Za-z0-9_-]+)[\"']?\s*(?:#.*)?$", re.M)


def fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def record(name):
    for d in RECORD_DIRS:
        try:
            with open(os.path.join(d, name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def newest(pattern, prefer=""):
    paths = []
    for root in HOME_ROOTS:
        paths.extend(glob.glob(os.path.join(root, "**", pattern), recursive=True))
    paths = [p for p in paths if os.path.isfile(p) and os.path.realpath(p) == p]
    hits = [p for p in paths if prefer and p.endswith(prefer)]
    pool = hits or paths
    return max(pool, key=os.path.getmtime) if pool else None


def jsonl(path):
    with open(path) as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def tool(name):
    return f"_[used tool: {name}]_"


def claude():
    sid = record("claude-code-resume.json").get("claude_session_id") or ""
    path = newest(".claude/projects/*/*.jsonl", f"/{sid}.jsonl" if sid else "")
    if not path:
        fail("no claude transcript found")
    out = []
    for d in jsonl(path):
        if d.get("type") not in ("user", "assistant") or d.get("isMeta"):
            continue
        m = d.get("message") or {}
        content = m.get("content")
        parts = []
        if isinstance(content, str):
            parts.append(content)
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "tool_use":
                parts.append(tool(c.get("name", "?")))
        text = "\n".join(p for p in parts if p.strip())
        if text:
            out.append((m.get("role") or d["type"], text))
    return out, path


def codex():
    tid = record("codex-resume.json").get("thread_id") or ""
    path = newest("sessions/*/*/*/rollout-*.jsonl", f"-{tid}.jsonl" if tid else "")
    if not path:
        fail("no codex rollout found")
    out = []
    for d in jsonl(path):
        if d.get("type") != "response_item":
            continue
        p = d.get("payload") or {}
        if p.get("type") == "function_call":
            out.append(("assistant", tool(p.get("name", "?"))))
        if p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
            continue
        text = "\n".join(c.get("text", "") for c in p.get("content") or [] if c.get("text"))
        # The harness's own context blocks (<environment_context>, ...) are not the user's words.
        if text.strip() and not text.lstrip().startswith("<"):
            out.append((p["role"], text))
    return out, path


def opencode():
    rec = record("opencode-resume.json")
    sid, home = rec.get("opencode_session_id"), rec.get("home")
    if not (sid and home):
        fail("no opencode resume record; cannot tell which session to export")
    st = os.stat(WORKSPACE)

    def demote():
        os.setgroups([])
        os.setgid(st.st_gid)
        os.setuid(st.st_uid)

    binary = shutil.which("opencode") or "/usr/local/bin/opencode"
    env = {"HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin"}
    res = subprocess.run([binary, "export", sid], cwd=WORKSPACE, env=env, preexec_fn=demote,
                         capture_output=True, timeout=120)
    body = res.stdout
    start = body.find(b"{")
    if res.returncode != 0 or start < 0:
        fail("opencode export failed: " + res.stderr.decode(errors="replace")[-400:])
    data = json.loads(body[start:])
    out = []
    for m in data.get("messages") or []:
        role = (m.get("info") or {}).get("role")
        parts = []
        for p in m.get("parts") or []:
            if p.get("type") == "text" and not p.get("synthetic"):
                parts.append(p.get("text", ""))
            elif p.get("type") == "tool":
                parts.append(tool(p.get("tool", "?")))
        text = "\n".join(t for t in parts if t.strip())
        if role in ("user", "assistant") and text:
            out.append((role, text))
    return out, f"opencode session {sid}"


def openhands():
    convs = glob.glob(os.path.join(WORKSPACE, "workspace", "conversations", "*", "events"))
    if not convs:
        fail("no openhands conversation found in the workspace")
    events = max(convs, key=os.path.getmtime)
    out = []
    for path in sorted(glob.glob(os.path.join(events, "event-*.json"))):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("kind") == "ActionEvent":
            out.append(("assistant", tool((d.get("tool_name") or "?"))))
        if d.get("kind") != "MessageEvent":
            continue
        lm = d.get("llm_message") or {}
        text = "\n".join(c.get("text", "") for c in lm.get("content") or [] if c.get("type") == "text")
        if text.strip():
            out.append(("user" if d.get("source") == "user" else "assistant", text))
    return out, events


EXPORTERS = {"claude-code": claude, "codex": codex, "opencode": opencode, "openhands": openhands}


def detect():
    for path in OHR_CONFIGS:
        try:
            with open(path) as f:
                names = set(RUNTIME_LINE.findall(f.read()))
        except OSError:
            continue
        if len(names) == 1 and next(iter(names)) in EXPORTERS:
            return names.pop()
        fail(f"{path} names {sorted(names) or 'no runtime'}; pass one of {', '.join(EXPORTERS)}")
    fail("no OHR config found; pass one of " + ", ".join(EXPORTERS))


def main():
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] not in EXPORTERS):
        fail(f"usage: export_transcript.py [{'|'.join(EXPORTERS)}]")
    adapter = sys.argv[1] if len(sys.argv) == 2 else detect()
    messages, source = EXPORTERS[adapter]()
    if not messages:
        fail(f"the {adapter} conversation in {source} has no messages")
    lines = [
        "# Previous conversation",
        "",
        f"Exported from an earlier {adapter} session. Messages are in order; tool calls are",
        "listed by name only, without their inputs or results.",
        "",
    ]
    for role, text in messages:
        lines += [f"## {'User' if role == 'user' else 'Assistant'}", "", text.strip(), ""]
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w") as f:
        f.write("\n".join(lines))
    st = os.stat(WORKSPACE)
    for p in (OUT_DIR, OUT):
        os.chown(p, st.st_uid, st.st_gid)
    print(json.dumps({"adapter": adapter, "source": source, "messages": len(messages), "path": OUT}))


if __name__ == "__main__":
    main()
