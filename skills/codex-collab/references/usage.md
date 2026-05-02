# Codex Collab Usage

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

## Statuses

Task statuses:

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

Coordinator queue states:

```text
pending     waiting for coordinator delivery
running     currently being delivered
retry       will be retried
delivered   coordinator was notified, task still appears unresolved
resolved    task no longer needs coordinator attention
failed      delivery failed too many times
```

## Common Commands

```bash
python .codex-collab/collab.py version
python .codex-collab/collab.py doctor
python .codex-collab/collab.py init
python .codex-collab/collab.py new-task --owner worker-a --title "Smoke test" --goal "Validate runner"
python .codex-collab/collab.py validate
python .codex-collab/collab.py dashboard
python .codex-collab/collab.py start-worker --worker worker-a --dry-run --once
python .codex-collab/collab.py run-coordinator --dry-run --once
python .codex-collab/collab.py repair-queue
python .codex-collab/collab.py review --include-failed --include-human
python .codex-collab/collab.py status
```

Install into a project:

```bash
python path/to/codex-collab/scripts/collab.py install --target /path/to/project --dashboard
```

Live readiness check:

```bash
python .codex-collab/collab.py doctor --live
```

Live worker:

```bash
python .codex-collab/collab.py start-worker --worker worker-a
```

Live coordinator:

```bash
python .codex-collab/collab.py run-coordinator
```

Live coordinator requires `config.json`:

```json
{
  "coordinator": {
    "sessionId": "main-coordinator-session-id"
  }
}
```

Clean test state:

```bash
python .codex-collab/collab.py clean --runs --state --reset-tasks --queue --force
```

## High-Risk Tasks

High-risk tasks or tasks with `requiresHumanApproval` cannot run until approved:

```bash
python .codex-collab/collab.py approve <task-id>
```

Use high risk for destructive changes, broad refactors, auth/payment/deployment/schema changes, live external systems, hardware, or ambiguous ownership.
