#!/usr/bin/env python3
"""Local agent — polls Supabase for pending code tasks, executes them via
Claude Code CLI, then commits and pushes.

Run on your Mac:
    cd local_agent && python agent.py

Requires: SUPABASE_URL, SUPABASE_KEY, PROJECT_DIR in .env (or env vars).
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import urllib.request
import urllib.parse

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
PROJECT_DIR = os.environ.get("PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")
AUTO_COMMIT = os.environ.get("AUTO_COMMIT", "true").lower() == "true"
AUTO_PUSH = os.environ.get("AUTO_PUSH", "true").lower() == "true"
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_OUTPUT = 10_000

db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def poll_for_task() -> dict | None:
    """Get the oldest pending code task."""
    resp = (
        db.table("code_tasks")
        .select("*")
        .eq("status", "pending")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def claim_task(task_id: str) -> bool:
    """Set task to in_progress (optimistic lock)."""
    try:
        db.table("code_tasks").update({
            "status": "in_progress",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).eq("status", "pending").execute()
        return True
    except Exception as e:
        log(f"Failed to claim task: {e}")
        return False


def execute_claude(instruction: str) -> tuple[bool, str]:
    """Run Claude Code CLI with the given instruction. Returns (success, output)."""
    cmd = [
        CLAUDE_CMD, "-p", instruction,
        "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
        "--permission-mode", "bypassPermissions",
    ]
    log(f"Executing: {CLAUDE_CMD} -p '...' (timeout={CLAUDE_TIMEOUT}s)")

    try:
        # Clean env to avoid "nested session" error if launched from Claude Code
        clean_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        result = subprocess.run(
            cmd,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            env=clean_env,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output[-MAX_OUTPUT:]  # Keep last 10K chars

        if result.returncode == 0:
            log("Claude Code completed successfully")
            return True, output
        else:
            log(f"Claude Code exited with code {result.returncode}")
            return False, output

    except subprocess.TimeoutExpired:
        log(f"Claude Code timed out after {CLAUDE_TIMEOUT}s")
        return False, f"Timed out after {CLAUDE_TIMEOUT}s"
    except FileNotFoundError:
        log(f"Claude CLI not found at '{CLAUDE_CMD}'. Is it installed and in PATH?")
        return False, f"Claude CLI not found: {CLAUDE_CMD}"
    except Exception as e:
        log(f"Execution error: {e}")
        return False, str(e)


def git_commit_and_push(title: str) -> str | None:
    """Stage, commit, and push changes. Returns commit hash or None."""
    try:
        # Check for changes
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_DIR, capture_output=True, text=True,
        )
        if not status.stdout.strip():
            log("No git changes to commit")
            return None

        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=PROJECT_DIR, check=True)

        # Commit
        msg = f"auto: {title[:60]}\n\nAutomated by AI self-improvement agent"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=PROJECT_DIR, check=True,
        )

        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR, capture_output=True, text=True,
        )
        commit_hash = result.stdout.strip()
        log(f"Committed: {commit_hash[:8]}")

        # Push
        if AUTO_PUSH:
            subprocess.run(["git", "push"], cwd=PROJECT_DIR, check=True)
            log("Pushed to remote")

        return commit_hash

    except subprocess.CalledProcessError as e:
        log(f"Git error: {e}")
        return None


def notify_telegram(success: bool, task_id: str, output: str, commit_hash: str | None) -> None:
    """Send a Telegram message directly via Bot API — instant notification."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram notification skipped (no token/chat_id)")
        return

    try:
        if success:
            emoji = "✅"
            summary = output.strip().split("\n")[-1][:200] if output else "Done"
            commit_str = f"\nCommit: {commit_hash[:8]}" if commit_hash else ""
            text = f"{emoji} Code task completed!\n{summary}{commit_str}"
        else:
            emoji = "❌"
            last_lines = "\n".join(output.strip().split("\n")[-3:])[:300] if output else "Unknown error"
            text = f"{emoji} Code task failed\nTask: {task_id[:8]}...\n{last_lines}"

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(url, data, timeout=10)
        log("Telegram notification sent")
    except Exception as e:
        log(f"Telegram notification failed: {e}")


def log_to_interaction_log(instruction: str, output: str, success: bool) -> None:
    """Log code task result to interaction_log so the regular Telegram chat has context."""
    try:
        summary = output[:500] if output else ("Done" if success else "Failed")
        db.table("interaction_log").insert({
            "user_id": int(TELEGRAM_CHAT_ID),
            "user_message": f"code {instruction[:200]}",
            "bot_response": summary,
            "action_type": "system",
        }).execute()
    except Exception as e:
        log(f"interaction_log write failed: {e}")


def complete_task(task_id: str, success: bool, output: str, commit_hash: str | None) -> None:
    """Update task status in Supabase."""
    status = "completed" if success else "failed"
    summary = output[:500] if output else ""

    try:
        update = {
            "status": status,
            "result_summary": summary,
            "claude_output": output[-MAX_OUTPUT:],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if commit_hash:
            update["git_commit_hash"] = commit_hash
        db.table("code_tasks").update(update).eq("id", task_id).execute()
        log(f"Task {task_id[:8]}... marked as {status}")
    except Exception as e:
        log(f"Failed to complete task: {e}")


def process_task(task: dict) -> None:
    """Full lifecycle: claim -> execute -> commit -> complete."""
    task_id = task["id"]
    instruction = task["instruction"]
    log(f"Processing task {task_id[:8]}...")

    if not claim_task(task_id):
        return

    success, output = execute_claude(instruction)

    commit_hash = None
    if success and AUTO_COMMIT:
        title = instruction.split("\n")[0][:60]
        commit_hash = git_commit_and_push(title)

    complete_task(task_id, success, output, commit_hash)
    notify_telegram(success, task_id, output, commit_hash)
    log_to_interaction_log(instruction, output, success)


def main() -> None:
    log(f"Agent started. Polling every {POLL_INTERVAL}s")
    log(f"Project dir: {PROJECT_DIR}")
    log(f"Claude cmd: {CLAUDE_CMD}")
    log(f"Auto-commit: {AUTO_COMMIT}, Auto-push: {AUTO_PUSH}")

    while True:
        try:
            task = poll_for_task()
            if task:
                process_task(task)
            else:
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("Shutting down...")
            break
        except Exception as e:
            log(f"Poll error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
