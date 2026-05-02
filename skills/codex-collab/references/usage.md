# Codex Collab Usage

Version: `0.1.1`

Codex-first local runner for a main Codex coordinator and one or more persistent Codex worker sessions.

The system is JSON-first:

- `tasks.json` is the source of truth for tasks, status, risk, ownership, and review pointers.
- `dashboard.md` is a rendered view for humans and the coordinator Codex to read.
- `runs/` stores execution evidence: task snapshot, logs, handoff, and worker outputs.
- `coordinator_queue.json` stores durable coordinator wakeup events.
- `collab.py` is the only CLI.

## Layout

```text
.codex-collab/
  collab.py                cross-platform CLI
  config.json              worker and coordinator settings
  tasks.json               task source of truth
  coordinator_queue.json   coordinator wakeup queue
  dashboard.md             generated operational dashboard
  runs/                    per-run task snapshots, logs, handoffs
  state/                   worker heartbeat, stop files, locks
```

The active queue of work is `tasks.json`; per-run evidence lives under `runs/`.

## Install Into A Project

Use Python 3.10+ on Windows, Linux, or macOS.

```bash
python path/to/codex-collab/scripts/collab.py install --target /path/to/project --dashboard
```

Then run from the target project:

```bash
python .codex-collab/collab.py doctor
python .codex-collab/collab.py validate
python .codex-collab/collab.py dashboard
```

Global CLI options must appear before the subcommand:

```bash
python .codex-collab/collab.py --root /path/to/project validate
```

Do not place them after the subcommand:

```bash
# Wrong: argparse treats this as a validate-specific option.
python .codex-collab/collab.py validate --root /path/to/project
```

## Daily Flow

```bash
python .codex-collab/collab.py init
python .codex-collab/collab.py doctor
python .codex-collab/collab.py new-task --owner worker-a --title "Smoke test" --goal "Validate JSON-first runner"
python .codex-collab/collab.py validate
python .codex-collab/collab.py dashboard
python .codex-collab/collab.py start-worker --worker worker-a --dry-run --once
python .codex-collab/collab.py run-coordinator --dry-run --once
python .codex-collab/collab.py review
python .codex-collab/collab.py dashboard
```

Check the installed version:

```bash
python .codex-collab/collab.py version
```

Run a self-check:

```bash
python .codex-collab/collab.py doctor
python .codex-collab/collab.py doctor --live
```

`doctor` is permissive for dry-run use. `doctor --live` treats missing live Codex settings, such as `coordinator.sessionId`, as errors.

## Live Use

Configure `.codex-collab/config.json`:

```json
{
  "workers": {
    "worker-a": {
      "cwd": ".",
      "useResume": true,
      "sessionId": "worker-codex-session-id",
      "sandbox": "workspace-write"
    }
  },
  "coordinator": {
    "sessionId": "main-coordinator-session-id"
  }
}
```

Live worker:

```bash
python .codex-collab/collab.py start-worker --worker worker-a
```

Live coordinator:

```bash
python .codex-collab/collab.py run-coordinator
```

Graceful stop:

```bash
python .codex-collab/collab.py stop-worker --worker worker-a
```

For real code changes, give each worker its own git worktree to avoid file conflicts.

## Task Status

```text
pending          ready for worker pickup
needs-approval   waiting for explicit approval
running          claimed by a worker
review           handoff ready for coordinator review
blocked          dependency or clarification needed
needs-human      worker needs a human decision
failed           failed, timed out, stale, or missing handoff
done             accepted or explicitly complete
parked           intentionally paused
```

High-risk tasks (`risk: high`) or tasks with `requiresHumanApproval: true` must have `approvedAt` before they can run.

Approve a task:

```bash
python .codex-collab/collab.py approve <task-id>
```

Move a task manually:

```bash
python .codex-collab/collab.py move <task-id> done
```

## Coordinator Queue States

```text
pending     waiting for coordinator delivery
running     currently being delivered
retry       will be retried
delivered   coordinator was notified, task still appears unresolved
resolved    task no longer needs coordinator attention
failed      delivery failed too many times
```

Test the queue without calling Codex:

```bash
python .codex-collab/collab.py run-coordinator --dry-run --once
```

Repair missing queue events from `tasks.json`:

```bash
python .codex-collab/collab.py repair-queue
```

## Why JSON First

Markdown is good for reading, but brittle as a database. `tasks.json` keeps the state machine stable:

- one source of truth
- easy validation
- no Markdown table parsing
- no extra conversion from dashboard to intermediate queue folders
- safe for Codex workers to consume directly

The dashboard is regenerated from `tasks.json`, worker state, and run artifacts:

```bash
python .codex-collab/collab.py dashboard
```

## High-Risk Tasks

Use high risk for destructive changes, broad refactors, auth/payment/deployment/schema changes, live external systems, hardware, or ambiguous ownership.

High-risk tasks require explicit approval:

```bash
python .codex-collab/collab.py approve <task-id>
```

## Reset Disposable Test State

```bash
python .codex-collab/collab.py clean --runs --state --reset-tasks --queue --force
```

`clean` refuses to run while tasks are marked `running` unless `--force` is supplied.

## Platform Notes

The runner logic is pure Python and path-portable. The intended OS target is Windows / macOS / Linux.

For live Codex execution, each machine needs:

- Python 3.10+
- `codex` CLI available on `PATH`
- `config.json` worker `cwd` set to that machine's project or worktree path

Use paths appropriate to the user's shell. Windows PowerShell, Bash on Linux, and zsh on macOS all work with the same runner.

On Windows, `codex` may resolve to an npm shim such as `codex.CMD`. Live worker and coordinator launches resolve the executable with `shutil.which("codex")`; `.cmd` and `.bat` shims are invoked through `cmd.exe`, while `.exe` and Unix executables are launched directly.

Live prompts are sent through stdin using `-`, not as one giant command-line argument. This keeps multiline prompts stable across Windows, macOS, and Linux. Captured CLI output is decoded as UTF-8 with replacement for invalid bytes, so non-ASCII CLI output should not crash the runner on GBK or other legacy Windows locales.

Codex Collab is Codex-first. The live worker and coordinator paths currently call Codex-specific commands such as `codex exec resume` and use Codex session ids and permission flags. Other Agent runtimes can port the JSON-first protocol, but need their own launch/resume adapter before they are live-compatible.
