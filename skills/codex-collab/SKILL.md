---
name: codex-collab
description: Set up and operate Codex Collab, a local JSON-first coordination skill that lets one main Codex act as a coordinator, decompose complex tasks, dispatch work to persistent worker Codex sessions, collect handoffs, maintain a dashboard, and review results. Use when the user wants a Codex-led delegation workflow, persistent worker sessions, coordinator/worker orchestration, durable handoff queues, dashboard/status tracking, or to install/package Codex Collab in a project.
---

# Codex Collab

Use this skill to install or operate a project-local `.codex-collab/` workspace. Codex Collab gives the main Codex a coordinator workflow: decompose a complex goal, create worker tasks, dispatch them to persistent Codex sessions, collect handoffs, review results, and keep the human-facing thread focused.

The underlying state is JSON-first: task truth in `tasks.json`, worker evidence in `runs/`, coordinator wakeup events in `coordinator_queue.json`, and a generated dashboard in `dashboard.md`.

Use this from the perspective of the main coordinator Codex. The human should not need to manually shuttle messages between worker windows. Keep parallel work low-coupling and route all handoffs back through the coordinator queue.

## Install In A Workspace

1. Locate the target project root.
2. Use the bundled installer:

```bash
python path/to/codex-collab/scripts/collab.py install --target /path/to/project --dashboard
```

3. Run from the target project:

```bash
python .codex-collab/collab.py doctor
python .codex-collab/collab.py validate
python .codex-collab/collab.py dashboard
```

Use Python 3.10+. The runner is cross-platform; adapt paths to the user's shell. If `install` cannot be used, create `.codex-collab/`, copy `scripts/collab.py` to `.codex-collab/collab.py`, then run `init`.

## Daily Workflow

Create a task:

```bash
python .codex-collab/collab.py new-task --owner worker-a --title "Task title" --goal "Concrete outcome"
```

Validate and render:

```bash
python .codex-collab/collab.py doctor
python .codex-collab/collab.py validate
python .codex-collab/collab.py dashboard
```

Run a worker once in test mode:

```bash
python .codex-collab/collab.py start-worker --worker worker-a --dry-run --once
```

Run the coordinator queue once in test mode:

```bash
python .codex-collab/collab.py run-coordinator --dry-run --once
```

Review handoffs:

```bash
python .codex-collab/collab.py review --include-failed --include-human
```

Reset disposable test state:

```bash
python .codex-collab/collab.py clean --runs --state --reset-tasks --queue --force
```

## Core Rules

- Act as the coordinator when the user asks for complex work: split the goal, create worker tasks, monitor status, review handoffs, and report decisions back to the user.
- Treat `tasks.json` as the task source of truth.
- Treat `coordinator_queue.json` as the coordinator wakeup source of truth.
- Treat `dashboard.md` as generated output, not an input database.
- Use `doctor` for local readiness and `doctor --live` before live worker/coordinator runs.
- Use `repair-queue` if a task needs coordinator attention but no queue event exists.
- Configure `config.json` `coordinator.sessionId` before live `run-coordinator`.
- Use dry-run commands before live worker or coordinator runs.
- Give real code-changing workers separate git worktrees when possible.

## When To Read References

- Read `references/usage.md` when explaining commands, onboarding a colleague, or setting up a project.
- Read `references/design.md` when reviewing reliability, failure modes, queue semantics, or long-running operation.

## Product Files

The reusable runner script is:

```text
scripts/collab.py
```

Install that script into a project's `.codex-collab/` directory rather than editing runtime files inside the skill unless the user is improving the product itself.
