#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
RUNNER_VERSION = "0.1.3"
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
REASONING_EFFORT_VALUES = {"minimal", "low", "medium", "high", "xhigh"}
SANDBOX_VALUES = {"read-only", "workspace-write", "danger-full-access"}
APPROVAL_POLICY_VALUES = {"untrusted", "on-failure", "on-request", "never"}
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


def codex_model_args(config: dict[str, Any]) -> list[str]:
    args: list[str] = []
    model = str(config.get("model", "")).strip()
    reasoning_effort = str(config.get("reasoningEffort", "")).strip()
    if model:
        args += ["-m", model]
    if reasoning_effort:
        args += ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    return args


def codex_live_args(config: dict[str, Any], cwd: str, allow_cd: bool = True) -> list[str]:
    args = codex_global_runtime_args(config, cwd if allow_cd else "")
    args += codex_model_args(config)
    if truthy(config.get("fullAuto")):
        args.append("--full-auto")
    return args


def merge_runtime_overrides(config: dict[str, Any], model: str = "", reasoning_effort: str = "") -> dict[str, Any]:
    merged = dict(config)
    if model:
        merged["model"] = model
    if reasoning_effort:
        merged["reasoningEffort"] = reasoning_effort
    return merged


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


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
            "cwd": str(root.parent),
            "sessionId": "",
            "model": "",
            "reasoningEffort": "",
            "sandbox": "workspace-write",
            "approvalPolicy": "",
            "search": False,
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
                "reasoningEffort": "",
                "useResume": False,
                "sessionId": "",
                "approvalPolicy": "",
                "search": False,
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
    (root / "reviews").mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    if not config_path.exists():
        write_json(config_path, default_config(root))
    else:
        config = load_json(config_path, {})
        changed = False
        defaults_all = default_config(root)
        if "coordinator" not in config or not isinstance(config.get("coordinator"), dict):
            config["coordinator"] = defaults_all["coordinator"]
            changed = True
        else:
            defaults = defaults_all["coordinator"]
            for key, value in defaults.items():
                if key not in config["coordinator"]:
                    config["coordinator"][key] = value
                    changed = True
        workers = config.get("workers")
        if not isinstance(workers, dict):
            config["workers"] = defaults_all["workers"]
            changed = True
        else:
            worker_defaults = defaults_all["workers"]["worker-a"]
            for worker_name, worker_cfg in list(workers.items()):
                if not isinstance(worker_cfg, dict):
                    continue
                for key, value in worker_defaults.items():
                    if key not in worker_cfg:
                        worker_cfg[key] = value
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


def stable_artifact_dirname(text: str, fallback: str, prefix_limit: int = 48, digest_len: int = 8) -> str:
    normalized = text.strip() or fallback
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:digest_len]
    prefix_budget = max(8, prefix_limit - digest_len - 1)
    prefix = slugify(normalized)[:prefix_budget].strip("-") or fallback
    return f"{prefix}-{digest}"


def make_task_id(title: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title)}"


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def worker_handoff_path(run_dir: Path) -> Path:
    return run_dir / "handoff.md"


def worker_prompt_path(run_dir: Path) -> Path:
    return run_dir / "worker-prompt.md"


def worker_task_snapshot_path(run_dir: Path) -> Path:
    return run_dir / "task.json"


def worker_last_message_path(run_dir: Path) -> Path:
    return run_dir / "last-message.md"


def worker_run_log_path(run_dir: Path) -> Path:
    return run_dir / "run.log"


def review_dir(root: Path, event: dict[str, Any]) -> Path:
    return root / "reviews" / stable_artifact_dirname(str(event.get("id", "")), "review")


def coordinator_event_snapshot_path(root: Path, event: dict[str, Any]) -> Path:
    return review_dir(root, event) / "event.json"


def coordinator_prompt_path(root: Path, event: dict[str, Any]) -> Path:
    return review_dir(root, event) / "coordinator-prompt.md"


def coordinator_last_message_path(root: Path, event: dict[str, Any]) -> Path:
    return review_dir(root, event) / "last-message.md"


def coordinator_run_log_path(root: Path, event: dict[str, Any]) -> Path:
    return review_dir(root, event) / "run.log"


def queue_event_artifact_fields(root: Path, event: dict[str, Any]) -> dict[str, str]:
    return {
        "reviewPath": rel(root, review_dir(root, event)),
        "eventPath": rel(root, coordinator_event_snapshot_path(root, event)),
        "promptPath": rel(root, coordinator_prompt_path(root, event)),
        "runLogPath": rel(root, coordinator_run_log_path(root, event)),
        "lastMessagePath": rel(root, coordinator_last_message_path(root, event)),
    }


def attach_queue_event_artifact_fields(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    event.update(queue_event_artifact_fields(root, event))
    return event


def sync_review_artifacts(root: Path, events: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for event in events:
        event_id = str(event.get("id", "")).strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        prepare_review_artifacts(root, event)


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


def resolve_cwd(root: Path, configured: Any, fallback: Path) -> str:
    configured = str(configured or "")
    if not configured:
        return str(fallback.resolve())
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = root.parent / path
    return str(path.resolve())


def worker_cwd(root: Path, worker_cfg: dict[str, Any]) -> str:
    return resolve_cwd(root, worker_cfg.get("cwd"), root.parent)


def coordinator_cwd(root: Path, coordinator_cfg: dict[str, Any]) -> str:
    return resolve_cwd(root, coordinator_cfg.get("cwd"), root.parent)


def codex_global_runtime_args(config: dict[str, Any], cwd: str) -> list[str]:
    args: list[str] = []
    if cwd:
        args += ["-C", cwd]
    sandbox = str(config.get("sandbox", "")).strip()
    if sandbox:
        args += ["-s", sandbox]
    approval_policy = str(config.get("approvalPolicy", "")).strip()
    if approval_policy:
        args += ["-a", approval_policy]
    if truthy(config.get("search")):
        args += ["--search"]
    return args


def process_exists(pid: Any) -> bool | None:
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_value <= 0:
        return None
    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


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


def make_queue_event(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id", ""))
    run_id = str(task.get("lastRunId") or task.get("currentRunId") or "")
    status = str(task.get("status", ""))
    event_id = queue_event_id(task_id, run_id, status)
    return attach_queue_event_artifact_fields(root, {
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
    })


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
    event = make_queue_event(root, task)
    event_to_sync: dict[str, Any] | None = None
    created = False
    with queue_lock(root):
        queue = load_queue(root)
        for existing in queue.get("events", []):
            if existing.get("id") != event["id"] or existing.get("state") not in QUEUE_ACTIVE_STATES:
                continue
            before = clone_json(existing)
            attach_queue_event_artifact_fields(root, existing)
            if existing != before:
                save_queue(root, queue)
            event_to_sync = clone_json(existing)
            break
        if event_to_sync is None:
            queue.setdefault("events", []).append(event)
            save_queue(root, queue)
            event_to_sync = clone_json(event)
            created = True
    if event_to_sync:
        sync_review_artifacts(root, [event_to_sync])
    return event["id"], created


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
    changed_events: list[dict[str, Any]] = []
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
            attach_queue_event_artifact_fields(root, event)
            changed_events.append(clone_json(event))
            changed = True
        if changed:
            save_queue(root, queue)
    sync_review_artifacts(root, changed_events)


def reconcile_queue_events(root: Path) -> list[str]:
    data = load_tasks(root)
    notify_statuses = coordinator_notify_statuses(root)
    tasks_by_id = {task.get("id"): task for task in data.get("tasks", []) if isinstance(task, dict)}
    resolved: list[str] = []
    changed_events: list[dict[str, Any]] = []
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
            attach_queue_event_artifact_fields(root, event)
            changed_events.append(clone_json(event))
            resolved.append(str(event.get("id", "")))
        if resolved:
            save_queue(root, queue)
    sync_review_artifacts(root, changed_events)
    return resolved


def claim_coordinator_event(root: Path, coordinator: dict[str, Any]) -> dict[str, Any] | None:
    max_attempts = int(coordinator.get("maxAttempts", 3) or 3)
    configured_statuses = coordinator.get("notifyStatuses", sorted(COORDINATOR_NOTIFY_STATUSES))
    notify_statuses = {str(status) for status in configured_statuses if str(status) in TASK_STATUSES} if isinstance(configured_statuses, list) else set(COORDINATOR_NOTIFY_STATUSES)
    if not notify_statuses:
        notify_statuses = set(COORDINATOR_NOTIFY_STATUSES)
    claimed_event: dict[str, Any] | None = None
    changed = False
    changed_events: list[dict[str, Any]] = []
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
                attach_queue_event_artifact_fields(root, event)
                changed_events.append(clone_json(event))
                changed = True
                continue
            task = tasks_by_id.get(event.get("taskId"))
            if not task or not event_matches_current_task_attention(event, task, notify_statuses):
                event["state"] = "resolved"
                event["updatedAt"] = iso_now()
                event["resolvedAt"] = iso_now()
                event["resolution"] = superseded_event_resolution(event, task, notify_statuses)
                attach_queue_event_artifact_fields(root, event)
                changed_events.append(clone_json(event))
                changed = True
                continue
            event["state"] = "running"
            event["attempts"] = int(event.get("attempts", 0) or 0) + 1
            event["claimedAt"] = iso_now()
            event["updatedAt"] = iso_now()
            attach_queue_event_artifact_fields(root, event)
            claimed_event = clone_json(event)
            changed_events.append(claimed_event)
            changed = True
            break
        if changed:
            save_queue(root, queue)
    sync_review_artifacts(root, changed_events)
    if claimed_event:
        return claimed_event
    return None


def peek_coordinator_event(root: Path, coordinator: dict[str, Any]) -> dict[str, Any] | None:
    max_attempts = int(coordinator.get("maxAttempts", 3) or 3)
    configured_statuses = coordinator.get("notifyStatuses", sorted(COORDINATOR_NOTIFY_STATUSES))
    notify_statuses = {str(status) for status in configured_statuses if str(status) in TASK_STATUSES} if isinstance(configured_statuses, list) else set(COORDINATOR_NOTIFY_STATUSES)
    if not notify_statuses:
        notify_statuses = set(COORDINATOR_NOTIFY_STATUSES)
    queue = load_queue(root)
    data = load_tasks(root)
    tasks_by_id = {task.get("id"): task for task in data.get("tasks", []) if isinstance(task, dict)}
    for event in queue.get("events", []):
        if event.get("state") not in {"pending", "retry"}:
            continue
        if int(event.get("attempts", 0) or 0) >= max_attempts:
            continue
        task = tasks_by_id.get(event.get("taskId"))
        if not task or not event_matches_current_task_attention(event, task, notify_statuses):
            continue
        preview = clone_json(event)
        attach_queue_event_artifact_fields(root, preview)
        return preview
    return None


def update_queue_event(root: Path, event_id: str, state: str, **fields: Any) -> None:
    updated_event: dict[str, Any] | None = None
    with queue_lock(root):
        queue = load_queue(root)
        for event in queue.get("events", []):
            if event.get("id") != event_id:
                continue
            event["state"] = state
            event["updatedAt"] = iso_now()
            event.update(fields)
            attach_queue_event_artifact_fields(root, event)
            updated_event = clone_json(event)
            save_queue(root, queue)
            break
    if not updated_event:
        raise SystemExit(f"Coordinator queue event not found: {event_id}")
    sync_review_artifacts(root, [updated_event])


def event_still_needs_coordinator(root: Path, event: dict[str, Any]) -> bool:
    data = load_tasks(root)
    task = find_task(data, str(event.get("taskId", "")))
    return bool(task and task_needs_coordinator(task, coordinator_notify_statuses(root)))


def print_task_preview(task: dict[str, Any] | None, worker: str) -> None:
    if not task:
        print(f"Dry-run preview: worker {worker!r} has no eligible pending task.")
        return
    print(f"Dry-run preview: worker {worker!r} would claim task {task.get('id')}.")
    print(f"  title: {task.get('title', '')}")
    print(f"  risk/status: {task.get('risk', '')}/{task.get('status', '')}")
    print("  no files, tasks, runs, queue events, or worker state were changed.")


def print_coordinator_event_preview(event: dict[str, Any] | None) -> None:
    if not event:
        print("Dry-run preview: no eligible coordinator queue event is ready.")
        return
    print(f"Dry-run preview: coordinator would process event {event.get('id')}.")
    print(f"  task/status: {event.get('taskId', '')}/{event.get('status', '')}")
    print(f"  state/attempts: {event.get('state', '')}/{event.get('attempts', 0)}")
    if event.get("reviewPath"):
        print(f"  review: {event.get('reviewPath')}")
    if event.get("promptPath"):
        print(f"  prompt: {event.get('promptPath')}")
    print("  no queue events, tasks, runs, or coordinator state were changed.")


def coordinator_prompt(root: Path, event: dict[str, Any]) -> str:
    run_path = root / "runs" / str(event.get("runId", ""))
    handoff_path = run_path / "handoff.md"
    return f"""You are the main coordinator in a JSON-first Codex collaboration system.

A worker event needs coordinator attention.

This system is not mainly a parallel-search tool. Its core is persistent worker identity.
Treat workers as continuing sessions that can be resumed, questioned, retried, or asked for a second pass.
Your job is to review the handoff, decide whether the same worker should continue, and write the next task state deliberately.
If the next step is still the same workstream, prefer reusing that worker instead of scattering the topic into new parallel branches.

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


def worker_prompt(root: Path, task: dict[str, Any], run_dir: Path, worker: str) -> str:
    handoff_path = worker_handoff_path(run_dir)
    task_snapshot = worker_task_snapshot_path(run_dir)
    prompt_path = worker_prompt_path(run_dir)
    return f"""You are {worker} in a JSON-first Codex collaboration system.

You are not a throwaway parallel search branch. You are a persistent worker identity that may be resumed later for follow-up questions or a second pass on the same line of work.
Preserve continuity in your handoff so the coordinator can send the same workstream back to you if needed.

Read:
- {prompt_path}
- {root / 'tasks.json'}
- {task_snapshot}
- {root / 'dashboard.md'}

Complete task {task['id']}. Write your final handoff to:
{handoff_path}

The handoff must include status, summary, changed files, validation commands and results, risks, review notes, and whether a human decision is needed.
Allowed handoff statuses are: done, blocked, needs-human, failed.
Do not edit tasks.json or dashboard.md unless the task explicitly asks for coordination-system changes.
Do not revert unrelated changes made by other workers or the user.
"""


def prepare_worker_artifacts(root: Path, task: dict[str, Any], run_dir: Path, worker: str) -> None:
    write_json(worker_task_snapshot_path(run_dir), clone_json(task))
    worker_prompt_path(run_dir).write_text(worker_prompt(root, task, run_dir, worker), encoding="utf-8")


def prepare_review_artifacts(root: Path, event: dict[str, Any]) -> tuple[Path, Path, Path, Path]:
    attach_queue_event_artifact_fields(root, event)
    artifact_dir = review_dir(root, event)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    event_snapshot = coordinator_event_snapshot_path(root, event)
    prompt_file = coordinator_prompt_path(root, event)
    output_last = coordinator_last_message_path(root, event)
    log_path = coordinator_run_log_path(root, event)
    write_json(event_snapshot, clone_json(event))
    prompt_file.write_text(coordinator_prompt(root, event), encoding="utf-8")
    if not log_path.exists():
        log_path.write_text("Coordinator review log will appear here.\n", encoding="utf-8")
    if not output_last.exists():
        output_last.write_text("Coordinator last message will appear here after a live resume.\n", encoding="utf-8")
    return event_snapshot, prompt_file, output_last, log_path


def run_coordinator_codex(root: Path, event: dict[str, Any], coordinator: dict[str, Any], timeout: int) -> tuple[int, Path]:
    session_id = str(coordinator.get("sessionId", "")).strip()
    if not session_id:
        raise SystemExit("config.json coordinator.sessionId is required for live run-coordinator. Use --dry-run for read-only preview or --exercise-flow for local queue rehearsal.")
    cwd = coordinator_cwd(root, coordinator)
    _, _, output_last, log_path = prepare_review_artifacts(root, event)
    prompt = coordinator_prompt(root, event)
    cmd = codex_live_args(coordinator, cwd)
    cmd += ["exec", "resume", "-o", str(output_last), "--skip-git-repo-check", session_id, "-"]
    return run_codex_cli(cmd, timeout, log_path, prompt=prompt), log_path


def process_coordinator_event(root: Path, event: dict[str, Any], coordinator: dict[str, Any], exercise_flow: bool, timeout: int) -> str:
    if exercise_flow:
        _, _, _, log_path = prepare_review_artifacts(root, event)
        log_path.write_text(f"Exercise-flow completed for {event['id']}.\n", encoding="utf-8")
        state = "delivered" if event_still_needs_coordinator(root, event) else "resolved"
        update_queue_event(
            root,
            event["id"],
            state,
            deliveredAt=iso_now(),
            lastError="",
            exerciseFlow=True,
            resolution="Exercise-flow did not invoke coordinator Codex." if state == "delivered" else "Task no longer needs coordinator attention.",
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
        for key in ["promptPath", "taskSnapshotPath", "runLogPath", "lastMessagePath"]:
            value = task.get(key)
            if value and not (root / str(value)).exists():
                issues.append({"level": "warning", "message": f"{task_id}: {key} does not exist: {value}."})
        handoff = task.get("handoffPath")
        if handoff and not (root / handoff).exists():
            issues.append({"level": "warning", "message": f"{task_id}: handoffPath does not exist: {handoff}."})
    return issues


def validate_codex_runtime_config(label: str, config: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    model = config.get("model", "")
    if model is not None and not isinstance(model, str):
        issues.append({"level": "error", "message": f"{label}.model must be a string."})
    reasoning_effort = config.get("reasoningEffort", "")
    if reasoning_effort is None:
        return issues
    if not isinstance(reasoning_effort, str):
        issues.append({"level": "error", "message": f"{label}.reasoningEffort must be a string."})
        return issues
    if reasoning_effort and reasoning_effort not in REASONING_EFFORT_VALUES:
        allowed = ", ".join(sorted(REASONING_EFFORT_VALUES))
        issues.append({"level": "warning", "message": f"{label}.reasoningEffort is '{reasoning_effort}'. Common values: {allowed}."})
    sandbox = config.get("sandbox", "")
    if sandbox is not None and not isinstance(sandbox, str):
        issues.append({"level": "error", "message": f"{label}.sandbox must be a string."})
    elif str(sandbox).strip() and str(sandbox).strip() not in SANDBOX_VALUES:
        allowed = ", ".join(sorted(SANDBOX_VALUES))
        issues.append({"level": "warning", "message": f"{label}.sandbox is '{sandbox}'. Expected one of: {allowed}."})
    approval_policy = config.get("approvalPolicy", "")
    if approval_policy is not None and not isinstance(approval_policy, str):
        issues.append({"level": "error", "message": f"{label}.approvalPolicy must be a string."})
    elif str(approval_policy).strip() and str(approval_policy).strip() not in APPROVAL_POLICY_VALUES:
        allowed = ", ".join(sorted(APPROVAL_POLICY_VALUES))
        issues.append({"level": "warning", "message": f"{label}.approvalPolicy is '{approval_policy}'. Expected one of: {allowed}."})
    search = config.get("search", False)
    if search is not None and not isinstance(search, (bool, str, int)):
        issues.append({"level": "error", "message": f"{label}.search must be a boolean-like value."})
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
        for key in ["reviewPath", "eventPath", "promptPath", "runLogPath", "lastMessagePath"]:
            value = str(event.get(key, "")).strip()
            if value and not (root / value).exists():
                issues.append({"level": "warning", "message": f"{event_id}: {key} does not exist: {value}."})
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
        issues.extend(validate_codex_runtime_config("coordinator", coordinator))
        for worker_name, worker_cfg in workers.items():
            issues.extend(validate_codex_runtime_config(f"worker {worker_name}", worker_cfg))
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
    coordinator_root = Path(coordinator_cwd(root, coordinator))
    level = "ok" if coordinator_root.exists() else "warning"
    issues.append({"level": level, "message": f"coordinator cwd: {coordinator_root} ({'found' if coordinator_root.exists() else 'missing'})."})
    coordinator_model = str(coordinator.get("model", "")).strip() or "default"
    coordinator_effort = str(coordinator.get("reasoningEffort", "")).strip() or "default"
    issues.append({"level": "ok", "message": f"coordinator model/reasoning: {coordinator_model}/{coordinator_effort}."})
    coordinator_sandbox = str(coordinator.get("sandbox", "")).strip() or "default"
    coordinator_approval = str(coordinator.get("approvalPolicy", "")).strip() or "default"
    coordinator_search = "on" if truthy(coordinator.get("search")) else "off"
    issues.append({"level": "ok", "message": f"coordinator live runtime: sandbox={coordinator_sandbox}, approval={coordinator_approval}, search={coordinator_search}."})

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
        worker_model = str(cfg.get("model", "")).strip() or "default"
        worker_effort = str(cfg.get("reasoningEffort", "")).strip() or "default"
        issues.append({"level": "ok", "message": f"worker {worker} model/reasoning: {worker_model}/{worker_effort}."})
        worker_sandbox = str(cfg.get("sandbox", "")).strip() or "default"
        worker_approval = str(cfg.get("approvalPolicy", "")).strip() or "default"
        worker_search = "on" if truthy(cfg.get("search")) else "off"
        issues.append({"level": "ok", "message": f"worker {worker} live runtime: sandbox={worker_sandbox}, approval={worker_approval}, search={worker_search}, resume={'on' if truthy(cfg.get('useResume')) else 'off'}."})
        state_path = root / "state" / f"{worker}.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                issues.append({"level": "warning", "message": f"worker {worker} state file unreadable: {exc}."})
            else:
                if state.get("status") == "running":
                    pid_state = process_exists(state.get("pid"))
                    if pid_state is False:
                        issues.append({"level": "warning", "message": f"worker {worker} state says running but pid {state.get('pid')} is not alive; stale recovery or manual move may be needed."})
                    elif pid_state is True:
                        issues.append({"level": "ok", "message": f"worker {worker} state says running and pid {state.get('pid')} is alive."})
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
            "promptPath": "",
            "taskSnapshotPath": "",
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
                "taskSnapshotPath": rel(root, worker_task_snapshot_path(run_dir)),
                "promptPath": rel(root, worker_prompt_path(run_dir)),
                "handoffPath": "",
                "runLogPath": "",
                "lastMessagePath": rel(root, worker_last_message_path(run_dir)),
            }
        )
        save_tasks(root, data)
        snapshot = json.loads(json.dumps(task))
    prepare_worker_artifacts(root, snapshot, run_dir, worker)
    return snapshot, run_dir


def peek_next_task(root: Path, worker: str) -> dict[str, Any] | None:
    data = load_tasks(root)
    candidates = [
        task for task in data.get("tasks", [])
        if task.get("owner") == worker and task.get("status") == "pending"
        and task_is_approved_for_run(task)
    ]
    candidates.sort(key=lambda task: str(task.get("createdAt", "")))
    if not candidates:
        return None
    return clone_json(candidates[0])


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
        run_dir = root / "runs" / run_id
        task["status"] = status
        task["currentRunId"] = ""
        task["lastRunId"] = run_id
        task["handoffPath"] = rel(root, handoff) if handoff.exists() else ""
        task["promptPath"] = rel(root, worker_prompt_path(run_dir)) if worker_prompt_path(run_dir).exists() else ""
        task["taskSnapshotPath"] = rel(root, worker_task_snapshot_path(run_dir)) if worker_task_snapshot_path(run_dir).exists() else ""
        task["runLogPath"] = rel(root, run_log) if run_log.exists() else ""
        task["lastMessagePath"] = rel(root, worker_last_message_path(run_dir)) if worker_last_message_path(run_dir).exists() else ""
        task["updatedAt"] = iso_now()
        for run in task.get("runs", []):
            if run.get("id") == run_id:
                run["status"] = status
                run["exitCode"] = exit_code
                run["finishedAt"] = iso_now()
                run["handoffPath"] = task["handoffPath"]
                run["promptPath"] = task["promptPath"]
                run["taskSnapshotPath"] = task["taskSnapshotPath"]
                run["runLogPath"] = task["runLogPath"]
                run["lastMessagePath"] = task["lastMessagePath"]
        completed_task = json.loads(json.dumps(task))
        save_tasks(root, data)
    if completed_task:
        enqueue_coordinator_event(root, completed_task)
    return status


def run_codex(root: Path, task: dict[str, Any], run_dir: Path, worker: str, worker_cfg: dict[str, Any], timeout: int) -> int:
    cwd = worker_cwd(root, worker_cfg)
    handoff_path = worker_handoff_path(run_dir)
    output_last = worker_last_message_path(run_dir)
    prompt = worker_prompt(root, task, run_dir, worker)
    cmd = codex_live_args(worker_cfg, cwd)
    cmd += ["exec"]
    if worker_cfg.get("useResume") and worker_cfg.get("sessionId"):
        cmd += ["resume", "-o", str(output_last), "--skip-git-repo-check", worker_cfg["sessionId"], "-"]
    else:
        cmd += ["-o", str(output_last), "--skip-git-repo-check", "-"]
    exit_code = run_codex_cli(cmd, timeout, worker_run_log_path(run_dir), prompt=prompt)
    if not handoff_path.exists() and output_last.exists():
        shutil.copyfile(output_last, handoff_path)
    return exit_code


def run_task(root: Path, worker: str, task: dict[str, Any], run_dir: Path, worker_cfg: dict[str, Any], exercise_flow: bool, timeout: int) -> str:
    task_id = task["id"]
    run_id = task["currentRunId"]
    write_state(root, worker, "exercise-flow" if exercise_flow else "live", "running", task_id, run_id)
    if exercise_flow:
        (run_dir / "handoff.md").write_text(
            render_handoff(
                task_id,
                "done",
                f"Exercise-flow completed. Worker {worker} claimed the JSON task without invoking Codex.",
                validation=f"Exercise-flow task transition succeeded. Timeout setting: {timeout} seconds.",
            ),
            encoding="utf-8",
        )
        (run_dir / "run.log").write_text(f"Exercise-flow completed for {task_id} by {worker}.\n", encoding="utf-8")
        exit_code = 0
    else:
        exit_code = run_codex(root, task, run_dir, worker, worker_cfg, timeout)
    status = finish_task(root, task_id, run_id, exit_code, run_dir / "handoff.md", run_dir / "run.log")
    write_state(root, worker, "exercise-flow" if exercise_flow else "live", "idle")
    return status


def command_start_worker(args) -> None:
    root = find_root(Path(args.root).resolve() if args.root else None)
    ensure_layout(root)
    require_valid(root)
    if args.dry_run and args.exercise_flow:
        raise SystemExit("--dry-run is read-only; --exercise-flow mutates state. Choose one.")
    config = load_json(root / "config.json", {})
    worker_cfg = config.get("workers", {}).get(args.worker, {})
    if not worker_cfg:
        raise SystemExit(f"Worker is not defined in config.json: {args.worker}")
    worker_cfg = merge_runtime_overrides(worker_cfg, args.model, args.reasoning_effort)
    poll = args.poll_seconds or int(worker_cfg.get("pollSeconds", 5))
    timeout = args.codex_timeout_seconds or int(worker_cfg.get("codexTimeoutSeconds", 3600))
    stale_minutes = args.stale_running_minutes or int(worker_cfg.get("staleRunningMinutes", 240))
    if args.dry_run:
        print_task_preview(peek_next_task(root, args.worker), args.worker)
        return
    mode = "exercise-flow" if args.exercise_flow else "live"
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
            status = run_task(root, args.worker, task, run_dir, worker_cfg, args.exercise_flow, timeout)
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
    if args.dry_run and args.exercise_flow:
        raise SystemExit("--dry-run is read-only; --exercise-flow mutates state. Choose one.")
    coordinator = get_coordinator(root)
    if not args.dry_run and not args.exercise_flow and not str(coordinator.get("sessionId", "")).strip():
        raise SystemExit("config.json coordinator.sessionId is required for live run-coordinator. Use --dry-run for read-only preview or --exercise-flow for local queue rehearsal.")
    coordinator = merge_runtime_overrides(coordinator, args.model, args.reasoning_effort)
    if args.dry_run:
        print_coordinator_event_preview(peek_coordinator_event(root, coordinator))
        return
    poll = args.poll_seconds or int(coordinator.get("pollSeconds", 5) or 5)
    timeout = args.codex_timeout_seconds or int(coordinator.get("codexTimeoutSeconds", 1800) or 1800)
    lease = args.lease_minutes or int(coordinator.get("leaseMinutes", 60) or 60)
    stop_file = root / "state" / "stop-coordinator"
    mode = "exercise-flow" if args.exercise_flow else "live"
    print(f"Coordinator runner watching coordinator_queue.json (mode={mode})")
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
        state = process_coordinator_event(root, event, coordinator, args.exercise_flow, timeout)
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
        prompt_path = root / task.get("promptPath", "")
        if task.get("promptPath") and prompt_path.exists() and prompt_path.is_file():
            print(f"Prompt: {prompt_path}")
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
        lines.append("| State | Event | Task | Status | Attempts | Review | Prompt | Updated |")
        lines.append("|---|---|---|---|---:|---|---|---|")
        for event in sorted(active_events, key=lambda item: item.get("updatedAt", item.get("createdAt", "")), reverse=True)[:recent_limit]:
            preview = clone_json(event)
            attach_queue_event_artifact_fields(root, preview)
            lines.append(f"| {escape_cell(preview.get('state'))} | {escape_cell(preview.get('id'))} | {escape_cell(preview.get('taskId'))} | {escape_cell(preview.get('status'))} | {escape_cell(preview.get('attempts'))} | {escape_cell(preview.get('reviewPath'))} | {escape_cell(preview.get('promptPath'))} | {escape_cell(preview.get('updatedAt'))} |")
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
        lines.append("| ID | Title | Owner | Prompt | Handoff |")
        lines.append("|---|---|---|---|---|")
        for task in review_rows:
            lines.append(f"| {escape_cell(task.get('id'))} | {escape_cell(task.get('title'))} | {escape_cell(task.get('owner'))} | {escape_cell(task.get('promptPath'))} | {escape_cell(task.get('handoffPath'))} |")
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
    new_task.add_argument("--goal", default="", help="One-sentence success condition for the worker")
    new_task.add_argument("--context", default="", help="Background, key facts, and the first files or folders the worker should read")
    new_task.add_argument("--mode", default="implement")
    new_task.add_argument("--risk", choices=sorted(RISK_VALUES), default="low")
    new_task.add_argument("--status", choices=TASK_STATUSES, default="")
    new_task.add_argument("--requires-human-approval", action="store_true")
    new_task.add_argument("--boundary", action="append", help="Constraint or non-goal; repeat for multiple boundaries")
    new_task.add_argument("--deliverable", action="append", help="Expected result or handoff content; repeat for multiple deliverables")
    new_task.add_argument("--validation", action="append", help="Required check to run, or acceptable explanation if no command can run; repeat for multiple checks")
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
    start.add_argument("--dry-run", action="store_true", help="Read-only preview of the next eligible task; does not claim or write state")
    start.add_argument("--exercise-flow", action="store_true", help="Mutating local rehearsal: claim a task, write fake run artifacts, and enqueue coordinator review without invoking Codex")
    start.add_argument("--once", action="store_true")
    start.add_argument("--poll-seconds", type=int, default=0)
    start.add_argument("--codex-timeout-seconds", type=int, default=0)
    start.add_argument("--stale-running-minutes", type=int, default=0)
    start.add_argument("--model", default="", help="Override this worker's Codex model for this run")
    start.add_argument("--reasoning-effort", choices=sorted(REASONING_EFFORT_VALUES), default="", help="Override this worker's Codex reasoning effort for this run")
    start.set_defaults(func=command_start_worker)

    stop = sub.add_parser("stop-worker")
    stop.add_argument("--worker", required=True)
    stop.set_defaults(func=command_stop)

    repair = sub.add_parser("repair-queue")
    repair.set_defaults(func=command_repair_queue)

    coordinator = sub.add_parser("run-coordinator")
    coordinator.add_argument("--dry-run", action="store_true", help="Read-only preview of the next eligible queue event; does not claim or update it")
    coordinator.add_argument("--exercise-flow", action="store_true", help="Mutating local rehearsal: claim and resolve/deliver a queue event without resuming Codex")
    coordinator.add_argument("--once", action="store_true")
    coordinator.add_argument("--poll-seconds", type=int, default=0)
    coordinator.add_argument("--codex-timeout-seconds", type=int, default=0)
    coordinator.add_argument("--lease-minutes", type=int, default=0)
    coordinator.add_argument("--model", default="", help="Override the coordinator Codex model for this run")
    coordinator.add_argument("--reasoning-effort", choices=sorted(REASONING_EFFORT_VALUES), default="", help="Override the coordinator Codex reasoning effort for this run")
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
