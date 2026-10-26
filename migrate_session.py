#!/usr/bin/env python3
"""Move one DigitalOcean Harness Runtime session to a new session.

    python3 migrate_session.py [OLD_SESSION_ID]

The new session is created from the old session's Agent Config, so it runs the
same agent with the same settings and secrets on the current session image.
The script:

  1. checks the old session,
  2. exports its conversation with export_transcript.py,
  3. downloads its workspace,
  4. creates the new session,
  5. uploads the workspace into the new session,
  6. sends a first message that tells the new agent to read the conversation,
  7. asks whether to remove the old session.

It runs on your computer and uses your doctl login. It writes a log and its
working files to ./migrate-<OLD_SESSION_ID>/. If a step fails, fix the cause
and run the same command again: finished steps are not repeated.
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request

EXPORT_SCRIPT = "export_transcript.py"
EXPORT_URL = (
    "https://raw.githubusercontent.com/digitalocean/harness-runtime-session-export/main/"
    + EXPORT_SCRIPT
)
TRANSCRIPT = "/workspace/.migrated-history/conversation.md"
STAGING = ".mars-migrate"
AGENTS = {
    "AGENT_KIND_CLAUDE_CODE": "claude-code",
    "AGENT_KIND_CODEX_CLI": "codex",
    "AGENT_KIND_OPENCODE": "opencode",
    "AGENT_KIND_OPENHANDS": "openhands",
}
READY = "SESSION_STATUS_READY"
PAUSED = "SESSION_STATUS_PAUSED"
GONE = ("SESSION_STATUS_DESTROYED", "SESSION_STATUS_FAILED")
HANDOFF = (
    "You may use tools now. The file {path} is our previous conversation, from another "
    "session. Read it and treat it as the earlier part of this conversation. Then reply "
    "with one sentence that says what we were working on last. Do not do anything else yet."
)
STEPS = 7


class StepError(Exception):
    """A step failed; the message says what to do about it."""


class Migration:
    def __init__(self, args):
        self.args = args
        self.old = args.session
        self.dir = os.path.abspath(args.work_dir or f"migrate-{self.old}")
        os.makedirs(self.dir, exist_ok=True)
        self.archive = os.path.join(self.dir, "workspace.tar")
        self.state_path = os.path.join(self.dir, "state.json")
        self.log_path = os.path.join(self.dir, "migrate.log")
        self.log_file = open(self.log_path, "a")
        self.state = self._load_state()

    # -- output -------------------------------------------------------------

    def log(self, msg):
        self.log_file.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
        self.log_file.flush()

    def say(self, msg):
        print(msg, flush=True)
        self.log(msg)

    def step(self, n, title):
        self.say(f"\n[{n}/{STEPS}] {title}")

    # -- state --------------------------------------------------------------

    def _load_state(self):
        try:
            with open(self.state_path) as f:
                state = json.load(f)
        except (OSError, ValueError):
            return {"old": self.old, "done": []}
        if state.get("old") != self.old:
            raise StepError(f"{self.state_path} belongs to session {state.get('old')}; "
                            "use another --work-dir")
        return state

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_path)

    def done(self, name):
        return name in self.state["done"]

    def mark(self, name):
        if name not in self.state["done"]:
            self.state["done"].append(name)
        self.save()

    # -- doctl --------------------------------------------------------------

    def doctl(self, command, *rest, check=True, timeout=None):
        """Run `doctl harness-runtime <command> [global flags] <rest>`."""
        argv = [self.args.doctl, "harness-runtime", command]
        if self.args.context:
            argv += ["--context", self.args.context]
        if self.args.api_url:
            argv += ["--api-url", self.args.api_url]
        argv += ["--interactive=false", *rest]
        shown = [a if len(a) < 200 else a[:40] + "...<script>" for a in argv]
        self.log("$ " + " ".join(shown))
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise StepError(f"cannot run {self.args.doctl}; install doctl or pass --doctl PATH")
        except subprocess.TimeoutExpired:
            raise StepError(f"`doctl harness-runtime {command}` did not finish in {timeout} seconds")
        if res.stdout.strip():
            self.log("stdout: " + res.stdout.strip()[-4000:])
        if res.stderr.strip():
            self.log("stderr: " + res.stderr.strip()[-4000:])
        self.log(f"exit {res.returncode}")
        if check and res.returncode != 0:
            raise StepError(f"`doctl harness-runtime {command}` failed: "
                            + error_detail(res.stderr.strip() or res.stdout.strip()))
        return res

    def show(self, session):
        res = self.doctl("show", session, "-o", "json")
        info = last_json(res.stdout)
        if not isinstance(info, dict):
            raise StepError(f"unexpected output from `doctl harness-runtime show {session}`")
        return info

    def wait_ready(self, session, minutes=10):
        deadline = time.time() + minutes * 60
        status = ""
        while time.time() < deadline:
            status = self.show(session).get("status", "")
            if status == READY:
                return
            if status in GONE:
                raise StepError(f"session {session} is {status}")
            time.sleep(5)
        raise StepError(f"session {session} is still {status} after {minutes} minutes")

    def guest(self, session, script, timeout=120):
        return self.doctl("exec", session, "--timeout", str(timeout), "--", "sh", "-c", script,
                          timeout=timeout + 60)

    # -- steps --------------------------------------------------------------

    def check_old(self):
        self.step(1, f"Checking session {self.old}")
        info = self.show(self.old)
        kind = info.get("agent_kind", "")
        if kind not in AGENTS:
            raise StepError(f"session {self.old} runs {kind or 'no agent'}; this script supports "
                            + ", ".join(AGENTS.values()))
        if not info.get("config_id"):
            raise StepError(f"session {self.old} has no Agent Config to create the new session from")
        status = info.get("status", "")
        if status == PAUSED and not self.done("download"):
            if not self.confirm(f"Session {self.old} is paused. Resume it so that it can be exported?",
                                default=True):
                raise StepError("the old session must be running; resume it and run again")
            self.doctl("resume", self.old)
            self.wait_ready(self.old)
            status = READY
        elif status != READY and not self.done("download"):
            raise StepError(f"session {self.old} is {status}; it must be READY")
        self.state.update(agent=AGENTS[kind], config_id=info["config_id"],
                          old_name=info.get("name") or self.old)
        self.save()
        self.say(f"  agent {self.state['agent']}, status {status}")

    def export(self):
        self.step(2, "Exporting the conversation")
        if self.done("export"):
            self.say("  already done")
            return
        res = self.doctl("exec", self.old, "--timeout", "300", "--",
                         "python3", "-c", self.export_script(), self.state["agent"],
                         check=False, timeout=400)
        result = last_json(res.stdout)
        if res.returncode != 0 or not isinstance(result, dict) or "error" in result:
            reason = (result or {}).get("error") or res.stderr.strip()[-300:] or "no output"
            raise StepError(f"the export failed in session {self.old}: {reason}")
        self.state["export"] = result
        self.mark("export")
        self.say(f"  {result['messages']} messages written to {result['path']}")

    def export_script(self):
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), EXPORT_SCRIPT)
        if os.path.isfile(local):
            with open(local) as f:
                return f.read()
        self.log(f"fetching {EXPORT_URL}")
        try:
            with urllib.request.urlopen(EXPORT_URL, timeout=30) as resp:
                return resp.read().decode()
        except OSError as e:
            raise StepError(f"cannot read {EXPORT_SCRIPT}; put it next to this script ({e})")

    def download(self):
        self.step(3, "Downloading the workspace")
        if self.done("download") and os.path.isfile(self.archive):
            self.say(f"  already done: {self.archive}")
            return
        partial = self.archive + ".part"
        self.doctl("download", self.old, "--workspace-path", "/workspace", "--archive",
                   "--save-to", partial)
        files, has_transcript = archive_summary(partial)
        if not has_transcript:
            raise StepError("the downloaded workspace has no conversation file; run again")
        os.replace(partial, self.archive)
        self.state["archive_files"] = files
        self.mark("download")
        self.say(f"  {files} files, {os.path.getsize(self.archive) // 1024} KiB")

    def create(self):
        self.step(4, "Creating the new session")
        new = self.state.get("new")
        if new:
            status = self.show(new).get("status", "")
            if status not in GONE:
                self.say(f"  already created: {self.state['new_name']} ({new})")
                self.wait_ready(new)
                return
            self.say(f"  session {new} from an earlier run is {status}; creating another")
            self.state["done"] = [d for d in self.state["done"] if d not in ("upload", "handoff")]
        name = self.args.name or f"{self.state['old_name']}-migrated-{time.strftime('%m%d%H%M')}"
        res = self.doctl("create", "--from-config", self.state["config_id"], "--name", name,
                         "-o", "json", timeout=300)
        info = last_json(res.stdout)
        new = isinstance(info, dict) and (info.get("session_id") or info.get("id"))
        if not new:
            raise StepError(f"`doctl harness-runtime create` printed no session ID; see {self.log_path}")
        self.state.update(new=new, new_name=name)
        self.save()
        self.say(f"  {name} ({new}); waiting until it is ready")
        self.wait_ready(new)
        node = self.guest(new, "node --version 2>/dev/null || echo none", timeout=60).stdout.strip()
        self.say(f"  ready; Node.js in the new session: {node}")

    def upload(self):
        self.step(5, "Uploading the workspace into the new session")
        new = self.state["new"]
        if self.done("upload"):
            self.say("  already done")
            return
        self.doctl("upload", new, "--local-file", self.archive, "--workspace-path", STAGING,
                   "--archive", timeout=3600)
        self.guest(new, f"cp -a /workspace/{STAGING}/workspace/. /workspace/ "
                        f"&& rm -rf /workspace/{STAGING}", timeout=600)
        check = self.guest(new, f"test -f {TRANSCRIPT} && echo found; find /workspace -type f | wc -l")
        words = check.stdout.split()
        if "found" not in words:
            raise StepError(f"{TRANSCRIPT} is missing in the new session after the upload")
        have, want = int(words[-1]), self.state.get("archive_files", 0)
        if have < want:
            raise StepError(f"the new session has {have} files; the old workspace had {want}")
        self.mark("upload")
        self.say(f"  {have} files in the new workspace")

    def handoff(self):
        self.step(6, "Sending the conversation to the new agent")
        if self.done("handoff"):
            self.say("  already done. The new agent said:\n    " + self.state.get("answer", ""))
            return
        rest = ["--timeout", "600", "-o", "json"]
        if not self.args.no_approve:
            rest += ["--on-hitl", "approve"]
        res = self.doctl("prompt", self.state["new"], *rest, HANDOFF.format(path=TRANSCRIPT),
                         check=False, timeout=700)
        out = last_json(res.stdout)
        out = out if isinstance(out, dict) else {}
        answer = (out.get("text") or "").strip()
        if res.returncode != 0 or not answer:
            raise StepError(f"the first message did not finish ({out.get('status') or res.returncode}); "
                            f"see {self.log_path}")
        self.state["answer"] = answer
        self.mark("handoff")
        self.say("  The new agent said:\n    " + answer.replace("\n", "\n    "))

    def finish(self):
        self.step(7, "Removing the old session")
        old = self.old
        if self.done("remove"):
            self.say("  already removed")
        elif self.args.keep_old:
            self.say(f"  kept. To remove it later: doctl harness-runtime remove {old}")
        elif self.confirm("Does the answer above match your old conversation? Remove the old "
                          f"session {old} now? This cannot be undone.", default=False):
            self.doctl("remove", old)
            self.mark("remove")
            self.say(f"  removed {old}")
        else:
            self.say(f"  kept. To remove it later: doctl harness-runtime remove {old}")
        self.say(f"\nDone. New session: {self.state['new_name']} ({self.state['new']})")
        self.say(f"Open it with: doctl harness-runtime launch {self.state['new']}")
        self.say(f"Working files: {self.dir}. workspace.tar there is a copy of your workspace; "
                 "remove the folder when you no longer need it.")

    def confirm(self, question, default):
        if self.args.yes and default:
            return True
        if not sys.stdin.isatty():
            self.say(f"  {question} -> {'yes' if default else 'no'} (not a terminal)")
            return default
        hint = "[Y/n]" if default else "[y/N]"
        try:
            reply = input(f"  {question} {hint} ").strip().lower()
        except EOFError:
            reply = ""
        self.log(f"confirm: {question} -> {reply!r}")
        return reply in ("y", "yes") if reply else default

    def run(self):
        self.say(f"Migrating session {self.old}. Log: {self.log_path}")
        if self.done("remove"):
            self.finish()
            return
        self.check_old()
        self.export()
        self.download()
        self.create()
        self.upload()
        self.handoff()
        self.finish()


def last_json(text):
    """The last line of text that is a JSON object, or the whole text as JSON."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    try:
        return json.loads(text)
    except ValueError:
        return None


def error_detail(text):
    """The API's error message from doctl output, or its last lines."""
    start = text.find('{"errors"')
    if start >= 0:
        try:
            errors = json.JSONDecoder().raw_decode(text[start:])[0]["errors"]
            return " ".join(e.get("detail") or e.get("title", "") for e in errors)
        except (ValueError, KeyError, TypeError):
            pass
    return " ".join(text.splitlines()[-3:])


def archive_summary(path):
    """(regular file count, whether the conversation file is in the archive)."""
    files, has_transcript = 0, False
    want = "workspace" + TRANSCRIPT[len("/workspace"):]
    try:
        with tarfile.open(path) as tar:
            for member in tar:
                if member.isfile():
                    files += 1
                    if member.name.lstrip("./") == want:
                        has_transcript = True
    except tarfile.TarError as e:
        raise StepError(f"the downloaded workspace is not a valid archive: {e}")
    return files, has_transcript


def main():
    p = argparse.ArgumentParser(description="Move one Harness Runtime session to a new session.")
    p.add_argument("session", nargs="?", help="ID or name of the session to move")
    p.add_argument("--name", help="name of the new session (default: <old name>-migrated-<time>)")
    p.add_argument("--keep-old", action="store_true", help="do not offer to remove the old session")
    p.add_argument("--no-approve", action="store_true",
                   help="do not approve tool requests during the first message")
    p.add_argument("--yes", action="store_true", help="resume a paused old session without asking")
    p.add_argument("--work-dir", help="folder for the log and working files")
    p.add_argument("--doctl", default=os.environ.get("DOCTL", "doctl"), help="path to doctl")
    p.add_argument("--context", help="doctl authentication context")
    p.add_argument("--api-url", help="DigitalOcean API endpoint")
    args = p.parse_args()
    if not args.session:
        if not sys.stdin.isatty():
            p.error("give the session ID")
        args.session = input("Session ID or name to move: ").strip()
        if not args.session:
            p.error("give the session ID")
    try:
        Migration(args).run()
    except StepError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        print("Fix the cause and run the same command again; finished steps are not repeated.",
              file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to continue", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
