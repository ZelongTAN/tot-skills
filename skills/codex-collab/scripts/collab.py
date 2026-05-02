#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PRODUCT_NAME = "Codex Collab"
CLI_NAME = "codex-collab"
RUNNER_VERSION = "0.1.1"
SCHEMA_VERSION = 1
RUNTIME_DIR = ".codex-collab"
LEGACY_RUNTIME_DIR = ".codex-collab"
INSTALLED_SCRIPT = "collab.py"
TASK_STATUSES = [
    "pending",
    "needs-approval",
    "running",
    "review",
    "blocked",
    "needs-human",
    "failed",
    "done",
    "parked",
]
ATTENTION_STATUSES = {"needs-approval", "needs-human", "blocked", "failed"}
RISK_VALUES = {"low", "medium", "high"}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$")
LOCK_STALE_SECONDS = 900
COORDINATOR_NOTIFY_STATUSES = {"review", "failed", "blocked", "needs-human"}
QUEUE_STATES = ["pending", "running", "retry", "delivered", "resolved", "failed"]
QUEUE_ACTIVE_STATES = {"pending", "retry", "running", "delivered"}
WINDOWS_CMD_EXTENSIONS = {".cmd", ".bat"}
ROOT_AFTER_SUBCOMMAND_HINT = (
    "Global options such as --root must appear before the subcommand. "
    f"Example: python {RUNTIME_DIR}/{INSTALLED_SCRIPT} --root <project-or-runtime-root> validate"
)


def now() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now().isoformat(timespec="seconds")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def shell_quote_for_log(args: list[str]) -> str:
    return " ".join(str(arg) if re.match(r"^[A-Za-z0-9_./:=+-]+$", str(arg)) else repr(str(arg)) for arg in args)


def resolve_codex_executable() -> Path:
    resolved = shutil.which("codex")
    if not resolved:
        raise FileNotFoundError("codex CLI not found on PATH.")
    return Path(resolved)


def build_codex_invocation(args: list[str]) -> tuple[list[str], list[str], Path]:
    executable = resolve_codex_executable()
    display_cmd = [str(executable), *args]
    if os.name == "nt" and executable.suffix.lower() in WINDOWS_CMD_EXTENSIONS:
        return ["cmd.exe", "/d", "/c", str(executable), *args], display_cmd, executable
    return [str(executable), *args], display_cmd, executable


def run_codex_cli(args: list[str], timeout: int, log_path: Path, prompt: str | None = None) -> int:
    try:
        cmd, display_cmd, executable = build_codex_invocation(args)
    except FileNotFoundError as exc:
        log_path.write_text(f"Codex CLI launch failed: {exc}\n", encoding="utf-8")
        return 127
    log_display = display_cmd
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout if timeout > 0 else None,
        )
        log_path.write_text(
            "\n".join(
                [
                    f"Command: {shell_quote_for_log(log_display)}",
                    f"ResolvedCodex: {executable}",
                    f"PromptSource: {'stdin' if prompt is not None else 'argv'}",
                    f"ExitCode: {proc.returncode}",
                    "",
                    "STDOUT:",
                    proc.stdout,
                    "STDERR:",
                    proc.stderr,
                ]
            ),
            encoding="utf-8",
        )
        return proc.returncode
    except PermissionError as exc:
        log_path.write_text(
            "\n".join(
                [
                    f"Command: {shell_quote_for_log(log_display)}",
                    f"ResolvedCodex: {executable}",
                    f"PromptSource: {'stdin' if prompt is not None else 'argv'}",
                    f"LaunchError: {type(exc).__name__}: {exc}",
                    "",
                    "On Windows, .cmd/.bat Codex launchers are invoked through cmd.exe automatically.",
                    "If this still fails, check file permissions and PATH resolution for the Codex CLI.",
                ]
            ),
            encoding="utf-8",
        )
        return 126
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        log_path.write_text(
            "\n".join(
                [
                    f"Command: {shell_quote_for_log(log_display)}",
                    f"ResolvedCodex: {executable}",
                    f"PromptSource: {'stdin' if prompt is not None else 'argv'}",
                    f"Timeout: {timeout} seconds",
                    "",
                    "STDOUT:",
                    str(stdout),
                    "STDERR:",
                    str(stderr),
                ]
            ),
            encoding="utf-8",
        )
        return 124


def find_root(start: Path | None = None) -> Path:
    if start:
        current = start.resolve()
    else:
        script_dir = Path(__file__).resolve().parent
        if script_dir.name in {RUNTIME_DIR, LEGACY_RUNTIME_DIR}:
            return script_dir
        current = Path.cwd().resolve()
    if current.name in {RUNTIME_DIR, LEGACY_RUNTIME_DIR}:
        return current
    for candidate in [current, *current.parents]:
        collab = candidate / RUNTIME_DIR
        if collab.exists():
            return collab.resolve()
    return current / RUNTIME_DIR


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def file_lock(root: Path, name: str, stale_seconds: int = LOCK_STALE_SECONDS):
    lock = root / "state" / name
    lock.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    acquired = False
    while not acquired:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "createdAt": iso_now()}))
            acquired = True
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_seconds:
                lock.unlink(missing_ok=True)
                continue
            if time.time() - start > 30:
                raise SystemExit(f"Timed out waiting for lock: {lock}")
            time.sleep(0.2)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def tasks_lock(root: Path):
    with file_lock(root, "tasks.lock"):
        yield


@contextmanager
def queue_lock(root: Path):
    with file_lock(root, "coordinator_queue.lock"):
        yield


def default_config(root: Path) -> dict[str, Any]:
    return {
        "coordinator": {
            "sessionId": "",
            "model": "",
            "pollSeconds": 5,
            "codexTimeoutSeconds": 1800,
            "maxAttempts": 3,
            "leaseMinutes": 60,
            "notifyStatuses": sorted(COORDINATOR_NOTIFY_STATUSES),
        },
        "workers": {
            "worker-a": {
                "cwd": str(root.parent),
                "model": "",
                "useResume": False,
                "sessionId": "",
                "pollSeconds": 5,
                "codexTimeoutSeconds": 3600,
                "staleRunningMinutes": 240,
                "sandbox": "workspace-write",
            }
        }
    }


def default_queue() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": iso_now(),
        "events": [],
    }


def default_tasks() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": iso_now(),
        "workspace": {
            "summary": "Codex Collab workspace. tasks.json is the source of truth; dashboard.md is rendered from it.",
            "strategy": [
                "Keep one main execution truth line.",
                "Treat parallel work as input and evidence, not automatic mainline implementation.",
                "Prefer parallel read-only research, test design, review, or low-coupling docs work.",
                "Do not split tightly coupled implementation across workers touching the same files.",
                "Review every handoff before accepting, retrying, or turning it into the next slice.",
            ],
            "focus": [
                {
                    "lane": "Main line",
                    "judgment": "Define the current critical path",
                    "owner": "coordinator",
                    "next": "Choose the next smallest executable slice",
                },
                {
                    "lane": "Parallel pool",
                    "judgment": "Keep only safe, low-coupling tasks",
                    "owner": "workers",
                    "next": "Run parallel work only when it does not disrupt main line",
                },
                {
                    "lane": "Review queue",
                    "judgment": "Handoffs and diffs waiting for inspection",
                    "owner": "coordinator",
                    "next": "Review, accept, retry, or reassign",
                },
            ],
            "decisions": [
                "Workers read tasks.json as the task source of truth.",
                "dashboard.md is a generated view and should not be used as an input database.",
                "High-risk work requires explicit approval before it can enter pending/running states.",
                "Coordinator wakeup uses an explicit durable queue after worker completion.",
            ],
            "escalation": [
                "Changing high-risk execution policy.",
                "Using broader filesystem or sandbox permissions.",
                "Running live external systems.",
                "Deleting, migrating, or rewriting large areas.",
                "Starting tightly coupled parallel implementation.",
            ],
        },
        "tasks": [],
    }


def ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    if not config_path.exists():
        write_json(config_path, default_config(root))
    else:
        config = load_json(config_path, {})
        changed = False
        if "coordinator" not in config or not isinstance(config.get("coordinator"), dict):
            config["coordinator"] = default_config(root)["coordinator"]
            changed = True
        else:
            defaults = default_config(root)["coordinator"]
            for key, value in defaults.items():
                if key not in config["coordinator"]:
                    config["coordinator"][key] = value
                    changed = True
        if changed:
            write_json(config_path, config)
    tasks_path = root / "tasks.json"
    if not tasks_path.exists():
        write_json(tasks_path, default_tasks())
    queue_path = root / "coordinator_queue.json"
    if not queue_path.exists():
        write_json(queue_path, default_queue())
    dashboard_path = root / "dashboard.md"
    if not dashboard_path.exists():
        dashboard_path.write_text(render_dashboard(root, recent_limit=30), encoding="utf-8")


def load_tasks(root: Path) -> dict[str, Any]:
    data = load_json(root / "tasks.json", default_tasks())
    if not isinstance(data, dict):
        raise SystemExit("tasks.json must contain a JSON object.")
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    data.setdefault("updatedAt", iso_now())
    data.setdefault("workspace", default_tasks()["workspace"])
    data.setdefault("tasks", [])
    return data


def save_tasks(root: Path, data: dict[str, Any]) -> None:
    data["schemaVersion"] = SCHEMA_VERSION
    data["updatedAt"] = iso_now()
    write_json(root / "tasks.json", data)


def load_queue(root: Path) -> dict[str, Any]:
    data = load_json(root / "coordinator_queue.json", default_queue())
    if not isinstance(data, dict):
        raise SystemExit("coordinator_queue.json must contain a JSON object.")
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    data.setdefault("updatedAt", iso_now())
    data.setdefault("events", [])
    return data


def save_queue(root: Path, data: dict[str, Any]) -> None:
    data["schemaVersion"] = SCHEMA_VERSION
    data["updatedAt"] = iso_now()
    write_json(root / "coordinator_queue.json", data)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (slug or "task")[:42].strip("-") or "task"


def make_task_id(title: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title)}"


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def normalize_list(values: list[str] | None, default: list[str]) -> list[str]:
    return [value for value in (values or default) if value]


def get_workers(root: Path) -> dict[str, Any]:
    config = load_json(root / "config.json", {})
    workers = config.get("workers", {})
    return workers if isinstance(workers, dict) else {}


def get_coordinator(root: Path) -> dict[str, Any]:
    config = load_json(root / "config.json", {})
    coordinator = config.get("coordinator", {})
    if not isinstance(coordinator, dict):
        coordinator = {}
    defaults = default_config(root)["coordinator"]
    return {**defaults, **coordinator}


def worker_cwd(root: Path, worker_cfg: dict[str, Any]) -> str:
    configured = str(worker_cfg.get("cwd") or "")
    if not configured:
        return str(root.parent)
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = root.parent / path
    return str(path.resolve())


def find_task(data: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in data.get("tasks", []):
        if task.get("id") == task_id:
            return task
    matches = [task for task in data.get("tasks", []) if task_id in str(task.get("id", ""))]
    return matches[0] if len(matches) == 1 else None


def task_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {status: sum(1 for task in tasks if task.get("status") == status) for status in TASK_STATUSES}


def progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return "[----------] 0%"
    percent = round(done / total * 100)
    filled = min(10, percent // 10)
    return "[" + "#" * filled + "-" * (10 - filled) + f"] {percent}%"


def escape_cell(value: Any) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ").strip()


def next_action(task: dict[str, Any]) -> str:
    status = task.get("status")
    return {
        "needs-approval": "Approve, rewrite, or park",
        "pending": "Wait for worker pickup",
        "running": "Monitor run heartbeat/log",
        "review": "Review handoff and diff",
        "needs-human": "Make the requested decision",
        "blocked": "Resolve dependency or clarify task",
        "failed": "Retry, reassign, rewrite, or stop",
        "done": "No action",
        "parked": "Leave parked until strategy changes",
    }.get(status, "Fix invalid task status")


def task_is_approved_for_run(task: dict[str, Any]) -> bool:
    approval_required = task.get("risk") == "high" or bool(task.get("requiresHumanApproval"))
    return not approval_required or bool(task.get("approvedAt"))


def task_needs_coordinator(task: dict[str, Any], statuses: set[str] | None = None) -> bool:
    notify_statuses = statuses or COORDINATOR_NOTIFY_STATUSES
    return str(task.get("status", "")) in notify_statuses


def coordinator_notify_statuses(root: Path) -> set[str]:
    configured = get_coordinator(root).get("notifyStatuses", sorted(COORDINATOR_NOTIFY_STATUSES))
    if not isinstance(configured, list):
        return set(COORDINATOR_NOTIFY_STATUSES)
    valid = {str(status) for status in configured if str(status) in TASK_STATUSES}
    return valid or set(COORDINATOR_NOTIFY_STATUSES)


def queue_event_id(task_id: str, run_id: str, status: str) -> str:
    return f"{task_id}:{run_id or 'no-run'}:{status}"


def queue_kind(status: str) -> str:
    return {
        "review": "handoff-review",
        "failed": "failure-review",
        "blocked": "blocked-review",
        "needs-human": "human-decision",
    }.get(status, "coordinator-review")


def queue_counts(queue: dict[str, Any]) -> dict[str, int]:
    return {state: sum(1 for event in queue.get("events", []) if event.get("state") == state) for state in QUEUE_STATES}


def active_event_exists(queue: dict[str, Any], event_id: str) -> bool:
    return any(event.get("id") == event_id and event.get("state") in QUEUE_ACTIVE_STATES for event in queue.get("events", []))


def make_queue_event(task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id", ""))
    run_id = str(task.get("lastRunId") or task.get("currentRunId") or "")
    status = str(task.get("status", ""))
    event_id = queue_event_id(task_id, run_id, status)
    return {
        "id": event_id,
        "taskId": task_id,
        "runId": run_id,
        "status": status,
        "kind": queue_kind(status),
        "state": "pending",
        "createdAt": iso_now(),
        "updatedAt": iso_now(),
        "attempts": 0,
        "lastError": "",
    }


def event_matches_current_task_attention(event: dict[str, Any], task: dict[str, Any], notify_statuses: set[str]) -> bool:
    if not task_needs_coordinator(task, notify_statuses):
        return False
    current_run = str(task.get("lastRunId") or task.get("currentRunId") or "")
    current_status = str(task.get("status", ""))
    return str(event.get("runId", "")) == current_run and str(event.get("status", "")) == current_status


def superseded_event_resolution(event: dict[str, Any], task: dict[str, Any] | None, notify_statuses: set[str]) -> str:
    if not task or not task_needs_coordinator(task, notify_statuses):
        return "Task no longer needs coordinator attention."
    return (
        "Queue event was superseded by a newer task run or status. "
        f"Current run/status: {task.get('lastRunId') or task.get('currentRunId') or 'no-run'}/{task.get('status', '')}; "
        f"event run/status: {event.get('runId') or 'no-run'}/{event.get('status', '')}."
    )


def enqueue_coordinator_event(root: Path, task: dict[str, Any]) -> tuple[str, bool] | tuple[None, False]:
    if not task_needs_coordinator(task, coordinator_notify_statuses(root)):
        return None, False
    event = make_queue_event(task)
    with queue_lock(root):
        queue = load_queue(root)
        if active_event_exists(queue, event["id"]):
            return event["id"], False
        queue.setdefault("events", []).append(event)
        save_queue(root, queue)
    return event["id"], True


def repair_queue(root: Path) -> tuple[list[str], list[str]]:
    data = load_tasks(root)
    notify_statuses = coordinator_notify_statuses(root)
    added: list[str] = []
    existing: list[str] = []
    for task in data.get("tasks", []):
        if not isinstance(task, dict) or not task_needs_coordinator(task, notify_statuses):
            continue
        event_id, created = enqueue_coordinator_event(root, task)
        if event_id:
            (added if created else existing).append(event_id)
    return added, existing


def recover_stale_queue_events(root: Path, lease_minutes: int) -> None:
    if lease_minutes <= 0:
        return
    cutoff = now() - timedelta(minutes=lease_minutes)
    changed = False
    with queue_lock(root):
        queue = load_queue(root)
        for event in queue.get("events", []):
            if event.get("state") != "running":
                continue
            claimed_at = parse_time(str(event.get("claimedAt", "")))
            if claimed_at and claimed_at >= cutoff:
                continue
            event["state"] = "retry"
            event["updatedAt"] = iso_now()
            event["lastError"] = "Coordinator event lease expired."
            changed = True
        if changed:
            save_queue(root, queue)


def reconcile_queue_events(root: Path) -> list[str]:
    data = load_tasks(root)
    notify_statuses = coordinator_notify_statuses(root)
    tasks_by_id = {task.get("id"): task for task in data.get("tasks", []) if isinstance(task, dict)}
    resolved: list[str] = []
    with queue_lock(root):
        queue = load_queue(root)
        for event in queue.get("events", []):
            if event.get("state") not in QUEUE_ACTIVE_STATES:
                continue
            task = tasks_by_id.get(event.get("taskId"))
            if task and event_matches_current_task_attention(event, task, notify_statuses):
                continue
            event["state"] = "resolved"
            event["updatedAt"] = iso_now()
            event["resolvedAt"] = iso_now()
            event["resolution"] = superseded_event_resolution(event, task, notify_statuses)
            resolved.append(str(event.get("id", "")))
        if resolved:
            save_queue(root, queue)
    return resolved


def claim_coordinator_event(root: Path, coordinator: dict[str, Any]) -> dict[str, Any] | None:
    max_attempts = int(coordinator.get("maxAttempts", 3) or 3)
    configured_statuses = coordinator.get("notifyStatuses", sorted(COORDINATOR_NOTIFY_STATUSES))
    notify_statuses = {str(status) for status in configured_statuses if str(status) in TASK_STATUSES} if isinstance(configured_statuses, list) else set(COORDINATOR_NOTIFY_STATUSES)
    if not notify_statuses:
        notify_statuses = set(COORDINATOR_NOTIFY_STATUSES)
    with queue_lock(root):
        queue = load_queue(root)
        data = load_tasks(root)
        tasks_by_id = {task.get("id"): task for task in data.get("tasks", []) if isinstance(task, dict)}
        for event in queue.get("events", []):
            if event.get("state") not in {"pending", "retry"}:
                continue
            if int(event.get("attempts", 0) or 0) >= max_attempts:
                event["state"] = "failed"
                event["updatedAt"] = iso_now()
                event["lastError"] = event.get("lastError") or "Max attempts exceeded."
                continue
            task = tasks_by_id.get(event.get("taskId"))
            if not task or not event_matches_current_task_attention(event, task, notify_statuses):
                event["state"] = "resolved"
                event["updatedAt"] = iso_now()
                event["resolvedAt"] = iso_now()
                event["resolution"] = superseded_event_resolution(event, task, notify_statuses)
                continue
            event["state"] = "running"
            event["attempts"] = int(event.get("attempts", 0) or 0) + 1
            event["claimedAt"] = iso_now()
            event["updatedAt"] = iso_now()
            save_queue(root, queue)
            return json.loads(json.dumps(event))
        save_queue(root, queue)
    return None


def update_queue_event(root: Path, event_id: str, state: str, **fields: Any) -> None:
    with queue_lock(root):
        queue = load_queue(root)
        for event in queue.get("events", []):
            if event.get("id") != event_id:
                continue
            event["state"] = state
            event["updatedAt"] = iso_now()
            event.update(fields)
            save_queue(root, queue)
            return
        raise SystemExit(f"Coordinator queue event not found: {event_id}")


def event_still_needs_coordinator(root: Path, event: dict[str, Any]) -> bool:
    data = load_tasks(root)
    task = find_task(data, str(event.get("taskId", "")))
    return bool(task and task_needs_coordinator(task, coordinator_notify_statuses(root)))


def coordinator_prompt(root: Path, event: dict[str, Any]) -> str:
    run_path = root / "runs" / str(event.get("runId", ""))
    handoff_path = run_path / "handoff.md"
    return f"""You are the main coordinator in a JSON-first Codex collaboration system.

A worker event needs coordinator attention.

Read:
- {root / 'tasks.json'}
- {root / 'dashboard.md'}
- {root / 'coordinator_queue.json'}
- {handoff_path}

Event:
- id: {event.get('id')}
- taskId: {event.get('taskId')}
- runId: {event.get('runId')}
- status: {event.get('status')}
- kind: {event.get('kind')}

Please review the task and handoff, then update the source of truth with one clear decision:
- move the task to done if accepted
- move it back to pending if it should be retried
- move it to blocked, needs-human, failed, or parked if appropriate
- create a follow-up task if useful

Do not treat this prompt as proof that the task is resolved. The queue runner will re-check tasks.json after you return.
"""


def run_coordinator_codex(root: Path, event: dict[str, Any], coordinator: dict[str, Any], timeout: int) -> tuple[int, Path]:
    session_id = str(coordinator.get("sessionId", "")).strip()
    if not session_id:
        raise SystemExit("config.json coordinator.sessionId is required for live run-coordinator. Use --dry-run to test queue flow.")
    log_dir = root / "state" / "coordinator-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output_last = log_dir / f"{slugify(event['id'])}-last-message.md"
    prompt = coordinator_prompt(root, event)
    cmd = ["exec", "resume"]
    if coordinator.get("model"):
        cmd += ["-m", str(coordinator["model"])]
    cmd += ["-o", str(output_last), "--skip-git-repo-check", session_id, "-"]
    log_path = log_dir / f"{slugify(event['id'])}.log"
    return run_codex_cli(cmd, timeout, log_path, prompt=prompt), log_path


def process_coordinator_event(root: Path, event: dict[str, Any], coordinator: dict[str, Any], dry_run: bool, timeout: int) -> str:
    if dry_run:
        state = "delivered" if event_still_needs_coordinator(root, event) else "resolved"
        update_queue_event(
            root,
            event["id"],
            state,
            deliveredAt=iso_now(),
            lastError="",
            dryRun=True,
            resolution="Dry-run did not invoke coordinator Codex." if state == "delivered" else "Task no longer needs coordinator attention.",
        )
        return state
    exit_code, log_path = run_coordinator_codex(root, event, coordinator, timeout)
    if exit_code != 0:
        max_attempts = int(coordinator.get("maxAttempts", 3) or 3)
        attempts = int(event.get("attempts", 1) or 1)
        state = "retry" if attempts < max_attempts else "failed"
        update_queue_event(
            root,
            event["id"],
            state,
            lastError=f"Coordinator resume failed with exit code {exit_code}.",
            runLogPath=rel(root, log_path),
        )
        return state
    if event_still_needs_coordinator(root, event):
        update_queue_event(root, event["id"], "delivered", deliveredAt=iso_now(), lastError="", runLogPath=rel(root, log_path))
        return "delivered"
    update_queue_event(
        root,
        event["id"],
        "resolved",
        resolvedAt=iso_now(),
        lastError="",
        runLogPath=rel(root, log_path),
        resolution="Task no longer needs coordinator attention after resume.",
    )
    return "resolved"


def validate_data(root: Path, data: dict[str, Any], workers: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if data.get("schemaVersion") != SCHEMA_VERSION:
        issues.append({"level": "error", "message": f"schemaVersion must be {SCHEMA_VERSION}."})
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return [{"level": "error", "message": "tasks must be a list."}]
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        prefix = f"tasks[{index}]"
        if not isinstance(task, dict):
            issues.append({"level": "error", "message": f"{prefix} must be an object."})
            continue
        task_id = str(task.get("id", ""))
        if not task_id:
            issues.append({"level": "error", "message": f"{prefix}.id is required."})
        elif not TASK_ID_PATTERN.match(task_id):
            issues.append({"level": "error", "message": f"{task_id}: id must use letters, numbers, '_' or '-' and be <= 80 chars."})
        elif task_id in seen:
            issues.append({"level": "error", "message": f"{task_id}: duplicate task id."})
        seen.add(task_id)
        for key in ["title", "owner", "goal"]:
            if not str(task.get(key, "")).strip():
                issues.append({"level": "error", "message": f"{task_id or prefix}: {key} is required."})
        owner = str(task.get("owner", ""))
        if owner and owner not in workers:
            issues.append({"level": "error", "message": f"{task_id}: owner '{owner}' is not defined in config.json workers."})
        status = str(task.get("status", ""))
        if status not in TASK_STATUSES:
            issues.append({"level": "error", "message": f"{task_id}: invalid status '{status}'."})
        risk = str(task.get("risk", ""))
        if risk not in RISK_VALUES:
            issues.append({"level": "error", "message": f"{task_id}: invalid risk '{risk}'."})
        approval_required = risk == "high" or bool(task.get("requiresHumanApproval"))
        already_approved = bool(task.get("approvedAt"))
        if approval_required and status in {"pending", "running", "review", "done"} and not already_approved:
            issues.append({"level": "error", "message": f"{task_id}: high-risk/approval-required task cannot run before approvedAt is set."})
        if status == "running" and not task.get("currentRunId"):
            issues.append({"level": "error", "message": f"{task_id}: running task must have currentRunId."})
        if status != "running" and task.get("currentRunId"):
            issues.append({"level": "warning", "message": f"{task_id}: currentRunId is set but status is {status}."})
        handoff = task.get("handoffPath")
        if handoff and not (root / handoff).exists():
            issues.append({"level": "warning", "message": f"{task_id}: handoffPath does not exist: {handoff}."})
    return issues


def validate_queue(root: Path, queue: dict[str, Any], tasks_data: dict[str, Any], coordinator: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if queue.get("schemaVersion") != SCHEMA_VERSION:
        issues.append({"level": "error", "message": f"coordinator_queue.json schemaVersion must be {SCHEMA_VERSION}."})
    events = queue.get("events")
    if not isinstance(events, list):
        return [{"level": "error", "message": "coordinator_queue.json events must be a list."}]
    tasks_by_id = {task.get("id"): task for task in tasks_data.get("tasks", []) if isinstance(task, dict)}
    configured_statuses = coordinator.get("notifyStatuses", sorted(COORDINATOR_NOTIFY_STATUSES))
    notify_statuses = {str(status) for status in configured_statuses if str(status) in TASK_STATUSES} if isinstance(configured_statuses, list) else set(COORDINATOR_NOTIFY_STATUSES)
    if not notify_statuses:
        notify_statuses = set(COORDINATOR_NOTIFY_STATUSES)
    seen: set[str] = set()
    lease_minutes = int(coordinator.get("leaseMinutes", 60) or 60)
    cutoff = now() - timedelta(minutes=lease_minutes)
    for index, event in enumerate(events):
        prefix = f"queue.events[{index}]"
        if not isinstance(event, dict):
            issues.append({"level": "error", "message": f"{prefix} must be an object."})
            continue
        event_id = str(event.get("id", ""))
        if not event_id:
            issues.append({"level": "error", "message": f"{prefix}.id is required."})
        elif event_id in seen:
            issues.append({"level": "error", "message": f"{event_id}: duplicate coordinator queue event id."})
        seen.add(event_id)
        state = str(event.get("state", ""))
        if state not in QUEUE_STATES:
            issues.append({"level": "error", "message": f"{event_id or prefix}: invalid queue state '{state}'."})
        task_id = str(event.get("taskId", ""))
        task = tasks_by_id.get(task_id)
        if not task:
            issues.append({"level": "error", "message": f"{event_id or prefix}: taskId does not exist: {task_id}."})
            continue
        status = str(event.get("status", ""))
        if status not in TASK_STATUSES:
            issues.append({"level": "error", "message": f"{event_id}: invalid referenced task status '{status}'."})
        expected_id = queue_event_id(task_id, str(event.get("runId", "")), status)
        if event_id and event_id != expected_id:
            issues.append({"level": "warning", "message": f"{event_id}: expected event id '{expected_id}'."})
        if state in {"pending", "retry", "running"} and not event_matches_current_task_attention(event, task, notify_statuses):
            issues.append({"level": "warning", "message": f"{event_id}: active queue event is stale or superseded; run repair-queue to resolve it."})
        if state == "resolved" and event_matches_current_task_attention(event, task, notify_statuses):
            issues.append({"level": "warning", "message": f"{event_id}: resolved queue event references a task that still needs coordinator attention."})
        if state == "running":
            claimed_at = parse_time(str(event.get("claimedAt", "")))
            if not claimed_at:
                issues.append({"level": "warning", "message": f"{event_id}: running event has no claimedAt."})
            elif claimed_at < cutoff:
                issues.append({"level": "warning", "message": f"{event_id}: running event is past lease timeout."})
    return issues


def has_errors(issues: list[dict[str, str]]) -> bool:
    return any(issue["level"] == "error" for issue in issues)


def print_issues(issues: list[dict[str, str]]) -> None:
    for issue in issues:
        print(f"{issue['level'].upper()}: {issue['message']}")


def require_valid(root: Path) -> dict[str, Any]:
    data = load_tasks(root)
    issues = validate_data(root, data, get_workers(root))
    issues.extend(validate_queue(root, load_queue(root), data, get_coordinator(root)))
    if has_errors(issues):
        print_issues(issues)
        raise SystemExit(1)
    return data


def command_init(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    print(f"Initialized {root}")
    print(f"Next: run `python {RUNTIME_DIR}/{INSTALLED_SCRIPT} validate` and `python {RUNTIME_DIR}/{INSTALLED_SCRIPT} dashboard`.")


def command_version(args) -> None:
    print(f"{CLI_NAME} {RUNNER_VERSION}")
    print(f"schemaVersion {SCHEMA_VERSION}")


def command_install(args) -> None:
    target = Path(args.target).expanduser().resolve()
    root = target if target.name in {RUNTIME_DIR, LEGACY_RUNTIME_DIR} else target / RUNTIME_DIR
    root.mkdir(parents=True, exist_ok=True)
    destination = root / INSTALLED_SCRIPT
    source = Path(__file__).resolve()
    same_file = False
    if destination.exists() and not args.force:
        try:
            same_file = source.samefile(destination)
        except FileNotFoundError:
            same_file = False
        if not same_file:
            raise SystemExit(f"{destination} already exists. Use --force to overwrite it.")
    elif destination.exists():
        try:
            same_file = source.samefile(destination)
        except FileNotFoundError:
            same_file = False
    if not same_file:
        shutil.copyfile(source, destination)
    ensure_layout(root)
    if args.dashboard:
        (root / "dashboard.md").write_text(render_dashboard(root, recent_limit=30), encoding="utf-8")
    print(f"Installed {PRODUCT_NAME} {RUNNER_VERSION} at {root}")
    print(f"Run: python {destination} validate")


def doctor_issues(root: Path, live: bool) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if sys.version_info < (3, 10):
        issues.append({"level": "error", "message": f"Python 3.10+ is required; current is {sys.version.split()[0]}."})
    else:
        issues.append({"level": "ok", "message": f"Python {sys.version.split()[0]}."})
    issues.append({"level": "ok", "message": f"Runner {RUNNER_VERSION}, schemaVersion {SCHEMA_VERSION}."})

    expected = ["config.json", "tasks.json", "coordinator_queue.json", "dashboard.md"]
    for name in expected:
        path = root / name
        level = "ok" if path.exists() else "error"
        issues.append({"level": level, "message": f"{name}: {'found' if path.exists() else 'missing'}."})

    try:
        data = load_tasks(root)
        workers = get_workers(root)
        coordinator = get_coordinator(root)
        issues.extend(validate_data(root, data, workers))
        issues.extend(validate_queue(root, load_queue(root), data, coordinator))
    except json.JSONDecodeError as exc:
        issues.append({"level": "error", "message": f"Invalid JSON: {exc}"})
        workers = {}
        coordinator = default_config(root)["coordinator"]

    codex_path = shutil.which("codex")
    if codex_path:
        issues.append({"level": "ok", "message": f"codex CLI found: {codex_path}."})
        if os.name == "nt" and Path(codex_path).suffix.lower() in WINDOWS_CMD_EXTENSIONS:
            issues.append({"level": "ok", "message": "Windows codex shim detected; live runner will invoke it through cmd.exe."})
    else:
        level = "error" if live else "warning"
        issues.append({"level": level, "message": "codex CLI not found on PATH; dry-run still works."})

    coordinator_session = str(coordinator.get("sessionId", "")).strip()
    if coordinator_session:
        issues.append({"level": "ok", "message": "coordinator.sessionId is configured."})
    else:
        level = "warning" if not live else "error"
        issues.append({"level": level, "message": "coordinator.sessionId is empty; live run-coordinator cannot resume the main session."})

    if not workers:
        issues.append({"level": "error", "message": "No workers are configured in config.json."})
    for worker, cfg in workers.items():
        if not isinstance(cfg, dict):
            issues.append({"level": "error", "message": f"Worker {worker} config must be an object."})
            continue
        cwd = Path(worker_cwd(root, cfg))
        level = "ok" if cwd.exists() else "warning"
        issues.append({"level": level, "message": f"worker {worker} cwd: {cwd} ({'found' if cwd.exists() else 'missing'})."})
        if cfg.get("useResume") and not str(cfg.get("sessionId", "")).strip():
            issues.append({"level": "error", "message": f"worker {worker} uses resume but sessionId is empty."})
    return issues


def command_doctor(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    issues = doctor_issues(root, live=args.live)
    for issue in issues:
        print(f"{issue['level'].upper()}: {issue['message']}")
    if has_errors([issue for issue in issues if issue["level"] in {"error", "warning"}]):
        raise SystemExit(1)


def command_validate(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    data = load_tasks(root)
    issues = validate_data(root, data, get_workers(root))
    issues.extend(validate_queue(root, load_queue(root), data, get_coordinator(root)))
    if issues:
        print_issues(issues)
    if has_errors(issues):
        raise SystemExit(1)
    print("OK: tasks.json and coordinator_queue.json are valid.")


def command_new_task(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    owner = args.owner or args.assignee
    if not owner:
        raise SystemExit("new-task requires --owner (or legacy --assignee).")
    status = args.status or ("needs-approval" if args.risk == "high" or args.requires_human_approval else "pending")
    task_id = args.id or make_task_id(args.title)
    with tasks_lock(root):
        data = load_tasks(root)
        if find_task(data, task_id):
            raise SystemExit(f"Task already exists: {task_id}")
        task = {
            "id": task_id,
            "title": args.title,
            "owner": owner,
            "mode": args.mode,
            "risk": args.risk,
            "status": status,
            "goal": args.goal or args.title,
            "context": args.context,
            "boundaries": normalize_list(args.boundary, ["Do not modify unrelated files.", "Do not revert changes made by others."]),
            "deliverables": normalize_list(args.deliverable, ["Complete the requested work.", "Write a handoff with summary, changed files, validation, risks, and review notes."]),
            "validation": normalize_list(args.validation, ["Run the smallest meaningful verification, or explain why it cannot run."]),
            "requiresHumanApproval": bool(args.requires_human_approval),
            "approvedAt": "",
            "createdAt": iso_now(),
            "updatedAt": iso_now(),
            "currentRunId": "",
            "lastRunId": "",
            "handoffPath": "",
            "runs": [],
            "reviewNotes": "",
        }
        data["tasks"].append(task)
        issues = validate_data(root, data, get_workers(root))
        if has_errors(issues):
            print_issues(issues)
            raise SystemExit(1)
        save_tasks(root, data)
    print(f"Created task: {task_id} ({status})")


def command_approve(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    with tasks_lock(root):
        data = load_tasks(root)
        task = find_task(data, args.task)
        if not task:
            raise SystemExit(f"Task not found: {args.task}")
        task["status"] = "pending"
        task["approvedAt"] = iso_now()
        task["approvedBy"] = args.by
        task["updatedAt"] = iso_now()
        save_tasks(root, data)
    print(f"Approved task: {task['id']} -> pending")


def command_move(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    with tasks_lock(root):
        data = load_tasks(root)
        task = find_task(data, args.task)
        if not task:
            raise SystemExit(f"Task not found: {args.task}")
        task["status"] = args.status
        task["updatedAt"] = iso_now()
        if args.status != "running":
            task["currentRunId"] = ""
        issues = validate_data(root, data, get_workers(root))
        if has_errors(issues):
            print_issues(issues)
            raise SystemExit(1)
        save_tasks(root, data)
    reconcile_queue_events(root)
    print(f"Moved task: {task['id']} -> {args.status}")


def write_state(root: Path, worker: str, mode: str, status: str, current_task: str = "", current_run: str = "") -> None:
    write_json(
        root / "state" / f"{worker}.json",
        {
            "worker": worker,
            "mode": mode,
            "status": status,
            "currentTask": current_task,
            "currentRun": current_run,
            "pid": os.getpid(),
            "updatedAt": iso_now(),
        },
    )


def command_status(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    reconcile_queue_events(root)
    data = load_tasks(root)
    tasks = data.get("tasks", [])
    if args.task:
        task = find_task(data, args.task)
        if not task:
            raise SystemExit(f"Task not found: {args.task}")
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return
    print("Task counts:")
    counts = task_counts(tasks)
    for status in TASK_STATUSES:
        print(f"{status}\t{counts[status]}")
    queue = load_queue(root)
    q_counts = queue_counts(queue)
    print("\nCoordinator queue counts:")
    for state in QUEUE_STATES:
        print(f"{state}\t{q_counts[state]}")
    states = sorted((root / "state").glob("*.json"))
    if args.worker:
        states = [root / "state" / f"{args.worker}.json"]
    print("\nWorker states:")
    found = False
    for state in states:
        if state.exists() and state.name != "tasks.lock":
            found = True
            print(state.read_text(encoding="utf-8").strip())
    if not found:
        print("No worker state files found.")


def render_handoff(task_id: str, status: str, summary: str, validation: str = "Not run", risks: str = "None noted", decision: str = "No") -> str:
    return f"""# Handoff: {task_id}

## Status

{status}

Allowed values: `done`, `blocked`, `needs-human`, `failed`.

## Summary

{summary}

## Changed Files

- None

## Validation

- {validation}

## Artifacts

- run.log

## Risks

- {risks}

## Review Notes

Review boundaries, validation, and risk notes.

## Human Decision Needed

- {decision}
"""


def make_run_id(task_id: str, worker: str) -> str:
    safe_worker = slugify(worker)
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task_id}-{safe_worker}"


def recover_stale(root: Path, worker: str, stale_minutes: int) -> None:
    if stale_minutes <= 0:
        return
    cutoff = now() - timedelta(minutes=stale_minutes)
    changed = False
    stale_tasks: list[dict[str, Any]] = []
    with tasks_lock(root):
        data = load_tasks(root)
        for task in data.get("tasks", []):
            if task.get("owner") != worker or task.get("status") != "running":
                continue
            updated = parse_time(str(task.get("updatedAt", "")))
            if updated and updated >= cutoff:
                continue
            run_id = task.get("currentRunId") or task.get("lastRunId")
            if run_id:
                run_dir = root / "runs" / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                handoff = run_dir / "handoff.md"
                if not handoff.exists():
                    handoff.write_text(
                        render_handoff(
                            task["id"],
                            "failed",
                            f"Marked failed as stale by worker {worker} at {iso_now()}.",
                            risks="Requires coordinator review",
                            decision="Yes",
                        ),
                        encoding="utf-8",
                    )
                task["handoffPath"] = rel(root, handoff)
            task["status"] = "failed"
            task["currentRunId"] = ""
            task["updatedAt"] = iso_now()
            stale_tasks.append(json.loads(json.dumps(task)))
            changed = True
        if changed:
            save_tasks(root, data)
    for task in stale_tasks:
        enqueue_coordinator_event(root, task)


def claim_next_task(root: Path, worker: str) -> tuple[dict[str, Any], Path] | tuple[None, None]:
    with tasks_lock(root):
        data = load_tasks(root)
        candidates = [
            task for task in data.get("tasks", [])
            if task.get("owner") == worker and task.get("status") == "pending"
            and task_is_approved_for_run(task)
        ]
        candidates.sort(key=lambda task: str(task.get("createdAt", "")))
        if not candidates:
            return None, None
        task = candidates[0]
        run_id = make_run_id(task["id"], worker)
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        task["status"] = "running"
        task["currentRunId"] = run_id
        task["lastRunId"] = run_id
        task["updatedAt"] = iso_now()
        task.setdefault("runs", []).append(
            {
                "id": run_id,
                "worker": worker,
                "status": "running",
                "startedAt": iso_now(),
                "path": rel(root, run_dir),
            }
        )
        save_tasks(root, data)
        snapshot = json.loads(json.dumps(task))
    write_json(run_dir / "task.json", snapshot)
    return snapshot, run_dir


def infer_completion_status(exit_code: int, handoff: Path) -> str:
    if exit_code != 0:
        return "failed"
    if handoff.exists():
        text = handoff.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"(?im)^##\s*Status\s*$\s*^([A-Za-z_-]+)\s*$", text)
        if not match:
            match = re.search(r"(?im)^status\s*:\s*([A-Za-z_-]+)\s*$", text)
        if match:
            value = match.group(1).strip().lower()
            if value in {"blocked", "needs-human", "failed"}:
                return value
    return "review"


def finish_task(root: Path, task_id: str, run_id: str, exit_code: int, handoff: Path, run_log: Path) -> str:
    status = infer_completion_status(exit_code, handoff)
    completed_task: dict[str, Any] | None = None
    with tasks_lock(root):
        data = load_tasks(root)
        task = find_task(data, task_id)
        if not task:
            raise SystemExit(f"Task disappeared while running: {task_id}")
        if task.get("currentRunId") != run_id:
            raise SystemExit(f"Refusing to finish {task_id}: currentRunId changed.")
        task["status"] = status
        task["currentRunId"] = ""
        task["lastRunId"] = run_id
        task["handoffPath"] = rel(root, handoff) if handoff.exists() else ""
        task["runLogPath"] = rel(root, run_log) if run_log.exists() else ""
        task["updatedAt"] = iso_now()
        for run in task.get("runs", []):
            if run.get("id") == run_id:
                run["status"] = status
                run["exitCode"] = exit_code
                run["finishedAt"] = iso_now()
                run["handoffPath"] = task["handoffPath"]
                run["runLogPath"] = task["runLogPath"]
        completed_task = json.loads(json.dumps(task))
        save_tasks(root, data)
    if completed_task:
        enqueue_coordinator_event(root, completed_task)
    return status


def run_codex(root: Path, task: dict[str, Any], run_dir: Path, worker: str, worker_cfg: dict[str, Any], timeout: int) -> int:
    cwd = worker_cwd(root, worker_cfg)
    handoff_path = run_dir / "handoff.md"
    output_last = run_dir / "last-message.md"
    task_snapshot = run_dir / "task.json"
    prompt = f"""You are {worker} in a JSON-first Codex collaboration system.

Source of truth:
- {root / 'tasks.json'}

Your assigned task snapshot:
- {task_snapshot}

Rendered overview, for reading only:
- {root / 'dashboard.md'}

Complete task {task['id']}. Write your final handoff to:
{handoff_path}

The handoff must include status, summary, changed files, validation commands and results, risks, review notes, and whether a human decision is needed.
Allowed handoff statuses are: done, blocked, needs-human, failed.
Do not edit tasks.json or dashboard.md unless the task explicitly asks for coordination-system changes.
Do not revert unrelated changes made by other workers or the user.
"""
    cmd = ["exec"]
    if worker_cfg.get("useResume") and worker_cfg.get("sessionId"):
        cmd += ["resume"]
        if worker_cfg.get("model"):
            cmd += ["-m", worker_cfg["model"]]
        cmd += ["-o", str(output_last), "--skip-git-repo-check", worker_cfg["sessionId"], "-"]
    else:
        if worker_cfg.get("model"):
            cmd += ["-m", worker_cfg["model"]]
        if worker_cfg.get("sandbox"):
            cmd += ["-s", worker_cfg["sandbox"]]
        cmd += ["-C", cwd, "-o", str(output_last), "--skip-git-repo-check", "-"]
    exit_code = run_codex_cli(cmd, timeout, run_dir / "run.log", prompt=prompt)
    if not handoff_path.exists() and output_last.exists():
        shutil.copyfile(output_last, handoff_path)
    return exit_code


def run_task(root: Path, worker: str, task: dict[str, Any], run_dir: Path, worker_cfg: dict[str, Any], dry_run: bool, timeout: int) -> str:
    task_id = task["id"]
    run_id = task["currentRunId"]
    write_state(root, worker, "dry-run" if dry_run else "live", "running", task_id, run_id)
    if dry_run:
        (run_dir / "handoff.md").write_text(
            render_handoff(
                task_id,
                "done",
                f"Dry-run completed. Worker {worker} claimed the JSON task without invoking Codex.",
                validation=f"Dry-run task transition succeeded. Timeout setting: {timeout} seconds.",
            ),
            encoding="utf-8",
        )
        (run_dir / "run.log").write_text(f"Dry-run completed for {task_id} by {worker}.\n", encoding="utf-8")
        exit_code = 0
    else:
        exit_code = run_codex(root, task, run_dir, worker, worker_cfg, timeout)
    status = finish_task(root, task_id, run_id, exit_code, run_dir / "handoff.md", run_dir / "run.log")
    write_state(root, worker, "dry-run" if dry_run else "live", "idle")
    return status


def command_start_worker(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    require_valid(root)
    config = load_json(root / "config.json", {})
    worker_cfg = config.get("workers", {}).get(args.worker, {})
    if not worker_cfg:
        raise SystemExit(f"Worker is not defined in config.json: {args.worker}")
    poll = args.poll_seconds or int(worker_cfg.get("pollSeconds", 5))
    timeout = args.codex_timeout_seconds or int(worker_cfg.get("codexTimeoutSeconds", 3600))
    stale_minutes = args.stale_running_minutes or int(worker_cfg.get("staleRunningMinutes", 240))
    mode = "dry-run" if args.dry_run else "live"
    stop_file = root / "state" / f"stop-{args.worker}"
    print(f"Worker {args.worker!r} watching tasks.json (mode={mode})")
    while True:
        if stop_file.exists():
            stop_file.unlink(missing_ok=True)
            write_state(root, args.worker, mode, "stopped")
            print(f"Stop requested for {args.worker}.")
            break
        recover_stale(root, args.worker, stale_minutes)
        write_state(root, args.worker, mode, "idle")
        task, run_dir = claim_next_task(root, args.worker)
        if not task:
            if args.once:
                break
            time.sleep(poll)
            continue
        print(f"Claimed task: {task['id']}")
        try:
            status = run_task(root, args.worker, task, run_dir, worker_cfg, args.dry_run, timeout)
            print(f"Finished task: {task['id']} -> {status}")
        except Exception as exc:
            (run_dir / "run.log").write_text(str(exc) + "\n", encoding="utf-8")
            (run_dir / "handoff.md").write_text(
                render_handoff(task["id"], "failed", "Worker exception occurred. See run.log.", risks="Requires coordinator review", decision="Yes"),
                encoding="utf-8",
            )
            finish_task(root, task["id"], task["currentRunId"], 1, run_dir / "handoff.md", run_dir / "run.log")
            write_state(root, args.worker, mode, "idle")
            print(f"Failed task: {task['id']}: {exc}", file=sys.stderr)
        if args.once:
            break
    if not stop_file.exists():
        write_state(root, args.worker, mode, "exited")


def command_stop(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    stop_file = root / "state" / f"stop-{args.worker}"
    stop_file.write_text(f"stop requested at {iso_now()}\n", encoding="utf-8")
    print(f"Stop requested for {args.worker}.")


def command_repair_queue(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    reconcile_queue_events(root)
    require_valid(root)
    added, existing = repair_queue(root)
    print(f"Repair queue scanned tasks. Added: {len(added)} Existing: {len(existing)}")
    for event_id in added:
        print(f"ADDED {event_id}")
    for event_id in existing:
        print(f"EXISTS {event_id}")


def command_run_coordinator(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    require_valid(root)
    coordinator = get_coordinator(root)
    if not args.dry_run and not str(coordinator.get("sessionId", "")).strip():
        raise SystemExit("config.json coordinator.sessionId is required for live run-coordinator. Use --dry-run to test queue flow.")
    poll = args.poll_seconds or int(coordinator.get("pollSeconds", 5) or 5)
    timeout = args.codex_timeout_seconds or int(coordinator.get("codexTimeoutSeconds", 1800) or 1800)
    lease = args.lease_minutes or int(coordinator.get("leaseMinutes", 60) or 60)
    stop_file = root / "state" / "stop-coordinator"
    print(f"Coordinator runner watching coordinator_queue.json (dry-run={args.dry_run})")
    while True:
        if stop_file.exists():
            stop_file.unlink(missing_ok=True)
            print("Stop requested for coordinator runner.")
            break
        reconcile_queue_events(root)
        repair_queue(root)
        recover_stale_queue_events(root, lease)
        event = claim_coordinator_event(root, coordinator)
        if not event:
            if args.once:
                break
            time.sleep(poll)
            continue
        print(f"Claimed coordinator event: {event['id']}")
        state = process_coordinator_event(root, event, coordinator, args.dry_run, timeout)
        print(f"Finished coordinator event: {event['id']} -> {state}")
        if args.once:
            break


def command_review(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    data = load_tasks(root)
    statuses = {"review"}
    if args.include_failed:
        statuses.add("failed")
    if args.include_human:
        statuses.update({"needs-human", "blocked", "needs-approval"})
    for task in sorted(data.get("tasks", []), key=lambda item: item.get("updatedAt", ""), reverse=True):
        if task.get("status") not in statuses:
            continue
        print(f"\n[{task['status'].upper()}] {task['id']} - {task.get('title', '')}")
        handoff = root / task.get("handoffPath", "")
        if handoff.exists() and handoff.is_file():
            print(f"Handoff: {handoff}")
            print("\n".join("  " + line for line in handoff.read_text(encoding="utf-8", errors="replace").splitlines()[:40]))
        else:
            print("Handoff: missing")


def render_dashboard(root: Path, recent_limit: int) -> str:
    data = load_tasks(root)
    queue = load_queue(root)
    workspace = data.get("workspace", {})
    tasks = sorted(data.get("tasks", []), key=lambda task: task.get("updatedAt", ""), reverse=True)
    counts = task_counts(tasks)
    q_counts = queue_counts(queue)
    total = len(tasks)
    attention = sum(counts[status] for status in ATTENTION_STATUSES)
    queue_attention = q_counts["pending"] + q_counts["retry"] + q_counts["running"] + q_counts["delivered"] + q_counts["failed"]
    lines: list[str] = []
    lines.append("# Codex Collab Dashboard")
    lines.append("")
    lines.append(f"Last generated: {iso_now()}")
    lines.append("")
    lines.append("Source of truth: `tasks.json`. This dashboard is a rendered view; do not use it as the input database.")
    lines.append("")
    lines.append("## Operating Flow")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart TD")
    lines.append('  A["Create or edit task in tasks.json / CLI"] --> B["validate"]')
    lines.append('  B --> C["start-worker claims pending JSON task"]')
    lines.append('  C --> D["run artifact under runs/"]')
    lines.append('  D --> E["worker handoff"]')
    lines.append('  E --> F["tasks.json status update"]')
    lines.append('  F --> G["dashboard render"]')
    lines.append('  B --> H["needs approval / invalid / blocked"]')
    lines.append("```")
    lines.append("")
    lines.append("## Current Strategy")
    lines.append("")
    for item in workspace.get("strategy", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Current Focus")
    lines.append("")
    focus = workspace.get("focus", [])
    if focus:
        lines.append("| Lane | Current Judgment | Owner | Next Step |")
        lines.append("|---|---|---|---|")
        for row in focus:
            lines.append(f"| {escape_cell(row.get('lane'))} | {escape_cell(row.get('judgment'))} | {escape_cell(row.get('owner'))} | {escape_cell(row.get('next'))} |")
    else:
        lines.append("No focus rows recorded.")
    lines.append("")
    lines.append("## Durable Decisions")
    lines.append("")
    for item in workspace.get("decisions", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Operational Progress")
    lines.append("")
    lines.append(f"- Overall: {progress_bar(counts['done'], total)}")
    lines.append(f"- Total tasks: {total}")
    lines.append(f"- Attention needed: {attention}")
    lines.append(f"- Coordinator queue attention: {queue_attention}")
    lines.append("")
    lines.append("| State | Count | Meaning |")
    lines.append("|---|---:|---|")
    meanings = {
        "needs-approval": "Waiting for approval before worker pickup",
        "pending": "Ready for worker pickup",
        "running": "Currently owned by a worker",
        "review": "Handoff ready for coordinator review",
        "needs-human": "Worker produced a decision point",
        "blocked": "Waiting on dependency or clarification",
        "failed": "Failed, timed out, stale, or missing handoff",
        "done": "Accepted or explicitly complete",
        "parked": "Intentionally paused",
    }
    for status in ["needs-approval", "pending", "running", "review", "needs-human", "blocked", "failed", "done", "parked"]:
        lines.append(f"| {status} | {counts[status]} | {meanings[status]} |")
    lines.append("")
    lines.append("## Coordinator Queue")
    lines.append("")
    lines.append("| State | Count | Meaning |")
    lines.append("|---|---:|---|")
    queue_meanings = {
        "pending": "Waiting for coordinator delivery",
        "running": "Currently being delivered to coordinator",
        "retry": "Will be retried",
        "delivered": "Coordinator was notified; task still appears unresolved",
        "resolved": "Task no longer needs coordinator attention",
        "failed": "Coordinator delivery failed too many times",
    }
    for state in QUEUE_STATES:
        lines.append(f"| {state} | {q_counts[state]} | {queue_meanings[state]} |")
    active_events = [event for event in queue.get("events", []) if event.get("state") in QUEUE_ACTIVE_STATES or event.get("state") == "failed"]
    if active_events:
        lines.append("")
        lines.append("| State | Event | Task | Status | Attempts | Updated |")
        lines.append("|---|---|---|---|---:|---|")
        for event in sorted(active_events, key=lambda item: item.get("updatedAt", item.get("createdAt", "")), reverse=True)[:recent_limit]:
            lines.append(f"| {escape_cell(event.get('state'))} | {escape_cell(event.get('id'))} | {escape_cell(event.get('taskId'))} | {escape_cell(event.get('status'))} | {escape_cell(event.get('attempts'))} | {escape_cell(event.get('updatedAt'))} |")
    lines.append("")
    lines.append("## Human Attention Queue")
    lines.append("")
    attention_rows = [task for task in tasks if task.get("status") in ATTENTION_STATUSES][:recent_limit]
    if not attention_rows:
        lines.append("No tasks currently require human attention.")
    else:
        lines.append("| Status | ID | Title | Owner | Risk | Next Action |")
        lines.append("|---|---|---|---|---|---|")
        for task in attention_rows:
            lines.append(f"| {escape_cell(task.get('status'))} | {escape_cell(task.get('id'))} | {escape_cell(task.get('title'))} | {escape_cell(task.get('owner'))} | {escape_cell(task.get('risk'))} | {escape_cell(next_action(task))} |")
    lines.append("")
    lines.append("## Task Board")
    lines.append("")
    if not tasks:
        lines.append("No tasks yet.")
    else:
        lines.append("| Status | ID | Title | Owner | Mode | Risk | Updated | Next Action |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for task in tasks[:recent_limit]:
            lines.append(f"| {escape_cell(task.get('status'))} | {escape_cell(task.get('id'))} | {escape_cell(task.get('title'))} | {escape_cell(task.get('owner'))} | {escape_cell(task.get('mode'))} | {escape_cell(task.get('risk'))} | {escape_cell(task.get('updatedAt'))} | {escape_cell(next_action(task))} |")
    lines.append("")
    lines.append("## Worker Status")
    lines.append("")
    states = []
    for state_file in sorted((root / "state").glob("*.json")):
        if state_file.name == "tasks.lock":
            continue
        try:
            states.append(json.loads(state_file.read_text(encoding="utf-8")))
        except Exception:
            states.append({"worker": state_file.stem, "mode": "unknown", "status": "unreadable", "currentTask": "", "currentRun": "", "pid": "", "updatedAt": ""})
    if not states:
        lines.append("No worker state files found.")
    else:
        lines.append("| Worker | Mode | Status | Current Task | Current Run | PID | Updated |")
        lines.append("|---|---|---|---|---|---:|---|")
        for state in states:
            lines.append(f"| {escape_cell(state.get('worker'))} | {escape_cell(state.get('mode'))} | {escape_cell(state.get('status'))} | {escape_cell(state.get('currentTask'))} | {escape_cell(state.get('currentRun'))} | {escape_cell(state.get('pid'))} | {escape_cell(state.get('updatedAt'))} |")
    lines.append("")
    lines.append("## Review Queue")
    lines.append("")
    review_rows = [task for task in tasks if task.get("status") == "review"][:recent_limit]
    if not review_rows:
        lines.append("No completed handoffs are waiting for review.")
    else:
        lines.append("| ID | Title | Owner | Handoff |")
        lines.append("|---|---|---|---|")
        for task in review_rows:
            lines.append(f"| {escape_cell(task.get('id'))} | {escape_cell(task.get('title'))} | {escape_cell(task.get('owner'))} | {escape_cell(task.get('handoffPath'))} |")
    lines.append("")
    lines.append("## Escalation Conditions")
    lines.append("")
    for item in workspace.get("escalation", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Next Controller Actions")
    lines.append("")
    if attention:
        lines.append("1. Resolve `needs-approval`, `needs-human`, `blocked`, and `failed` tasks in `tasks.json`.")
    elif counts["review"]:
        lines.append("1. Review handoffs and move accepted tasks to `done`.")
    elif counts["pending"]:
        lines.append("1. Start or resume workers for pending tasks.")
    else:
        lines.append("1. Create or unpark the next task.")
    lines.append(f"2. Run `python {RUNTIME_DIR}/{INSTALLED_SCRIPT} validate` before long worker runs.")
    lines.append(f"3. Run `python {RUNTIME_DIR}/{INSTALLED_SCRIPT} dashboard` after task state changes.")
    lines.append("")
    return "\n".join(lines)


def command_dashboard(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    reconcile_queue_events(root)
    require_valid(root)
    path = root / "dashboard.md"
    path.write_text(render_dashboard(root, args.recent_limit), encoding="utf-8")
    print(f"Updated dashboard: {path}")


def command_clean(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    if not args.force:
        running = [task["id"] for task in load_tasks(root).get("tasks", []) if task.get("status") == "running"]
        if running:
            raise SystemExit(f"Refusing to clean while tasks are running: {', '.join(running)}. Use --force to override.")
    if args.runs:
        runs = root / "runs"
        if runs.exists():
            for child in runs.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    if args.state:
        state = root / "state"
        if state.exists():
            for child in state.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    if args.reset_tasks:
        write_json(root / "tasks.json", default_tasks())
    if args.queue:
        write_json(root / "coordinator_queue.json", default_queue())
    print("Clean complete.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JSON-first cross-platform Codex collaboration runner")
    parser.add_argument("--root", default="", help=f"Path to {RUNTIME_DIR} or project root")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.set_defaults(func=command_init)

    version = sub.add_parser("version")
    version.set_defaults(func=command_version)

    install = sub.add_parser("install")
    install.add_argument("--target", required=True, help=f"Project root or {RUNTIME_DIR} directory")
    install.add_argument("--force", action="store_true", help=f"Overwrite an existing {INSTALLED_SCRIPT}")
    install.add_argument("--dashboard", action="store_true", help="Render dashboard.md after install")
    install.set_defaults(func=command_install)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--live", action="store_true", help="Treat missing live Codex settings as errors")
    doctor.set_defaults(func=command_doctor)

    validate = sub.add_parser("validate")
    validate.set_defaults(func=command_validate)

    new_task = sub.add_parser("new-task")
    new_task.add_argument("--id", default="")
    new_task.add_argument("--owner", default="")
    new_task.add_argument("--assignee", default="", help="Legacy alias for --owner")
    new_task.add_argument("--title", required=True)
    new_task.add_argument("--goal", default="")
    new_task.add_argument("--context", default="")
    new_task.add_argument("--mode", default="implement")
    new_task.add_argument("--risk", choices=sorted(RISK_VALUES), default="low")
    new_task.add_argument("--status", choices=TASK_STATUSES, default="")
    new_task.add_argument("--requires-human-approval", action="store_true")
    new_task.add_argument("--boundary", action="append")
    new_task.add_argument("--deliverable", action="append")
    new_task.add_argument("--validation", action="append")
    new_task.set_defaults(func=command_new_task)

    approve = sub.add_parser("approve")
    approve.add_argument("task")
    approve.add_argument("--by", default="coordinator")
    approve.set_defaults(func=command_approve)

    move = sub.add_parser("move")
    move.add_argument("task")
    move.add_argument("status", choices=TASK_STATUSES)
    move.set_defaults(func=command_move)

    start = sub.add_parser("start-worker")
    start.add_argument("--worker", required=True)
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--once", action="store_true")
    start.add_argument("--poll-seconds", type=int, default=0)
    start.add_argument("--codex-timeout-seconds", type=int, default=0)
    start.add_argument("--stale-running-minutes", type=int, default=0)
    start.set_defaults(func=command_start_worker)

    stop = sub.add_parser("stop-worker")
    stop.add_argument("--worker", required=True)
    stop.set_defaults(func=command_stop)

    repair = sub.add_parser("repair-queue")
    repair.set_defaults(func=command_repair_queue)

    coordinator = sub.add_parser("run-coordinator")
    coordinator.add_argument("--dry-run", action="store_true")
    coordinator.add_argument("--once", action="store_true")
    coordinator.add_argument("--poll-seconds", type=int, default=0)
    coordinator.add_argument("--codex-timeout-seconds", type=int, default=0)
    coordinator.add_argument("--lease-minutes", type=int, default=0)
    coordinator.set_defaults(func=command_run_coordinator)

    status = sub.add_parser("status")
    status.add_argument("--worker", default="")
    status.add_argument("--task", default="")
    status.set_defaults(func=command_status)

    review = sub.add_parser("review")
    review.add_argument("--include-failed", action="store_true")
    review.add_argument("--include-human", action="store_true")
    review.set_defaults(func=command_review)

    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--recent-limit", type=int, default=30)
    dashboard.set_defaults(func=command_dashboard)

    clean = sub.add_parser("clean")
    clean.add_argument("--runs", action="store_true")
    clean.add_argument("--state", action="store_true")
    clean.add_argument("--reset-tasks", action="store_true")
    clean.add_argument("--queue", action="store_true")
    clean.add_argument("--force", action="store_true")
    clean.set_defaults(func=command_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(raw_args)
    except SystemExit:
        subcommands: set[str] = set()
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                subcommands.update(str(choice) for choice in choices)
        for index, value in enumerate(raw_args):
            if value in subcommands and "--root" in raw_args[index + 1:]:
                print(f"\nHint: {ROOT_AFTER_SUBCOMMAND_HINT}", file=sys.stderr)
                break
        raise
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
