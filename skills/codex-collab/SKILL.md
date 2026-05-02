---
name: codex-collab
description: Set up and operate Codex Collab, a Codex-first JSON coordination skill for complex work. Use when the user wants one main Codex to act as coordinator, split a goal into worker tasks, dispatch persistent Codex worker sessions, collect handoffs through a durable queue, maintain a dashboard, review results, or install/package the local runner. The protocol can be ported to other Agent runtimes, but this implementation is built around Codex CLI/session resume.
---

# Codex Collab

Give one main Codex a coordinator desk.

The user should keep talking to one main Codex. That coordinator Codex uses a project-local `.codex-collab/` workspace to split work, assign persistent worker Codex sessions, collect handoffs, review results, and keep the dashboard current. The human should not become the message bus.

Codex Collab is JSON-first:

- `tasks.json` is the task source of truth.
- `coordinator_queue.json` is the coordinator wakeup source of truth.
- `runs/` stores worker evidence: task snapshots, logs, and handoffs.
- `dashboard.md` is a generated view for humans and the coordinator to scan.

## First, Set The Boundary

This implementation is Codex-first. Live worker and coordinator wakeups use the `codex` CLI, Codex session ids, `codex exec resume`, and Codex permission flags.

The file protocol itself is portable. Other Agent runtimes can reuse the JSON-first task/queue/dashboard design, but they need their own launch, resume, permission, and session-management adapters.

The runner is intended to work across Windows / macOS / Linux because it is pure Python plus local files.

## Fast Start

Install into the target project:

```bash
python path/to/codex-collab/scripts/collab.py install --target /path/to/project --dashboard
```

Then run from that project:

```bash
python .codex-collab/collab.py doctor
python .codex-collab/collab.py validate
python .codex-collab/collab.py dashboard
```

When using global options, place them before the subcommand:

```bash
python .codex-collab/collab.py --root /path/to/project validate
```

If the installer is not available, create `.codex-collab/`, copy `scripts/collab.py` to `.codex-collab/collab.py`, then run:

```bash
python .codex-collab/collab.py init
```

## Coordinator Playbook

When the user gives a complex task:

1. Act as the coordinator, not as a one-session hero.
2. Break the goal into small, low-coupling worker tasks.
3. Keep code-changing workers in separate git worktrees when possible.
4. Create tasks with clear owners, goals, and review expectations.
5. Run dry-run commands before live worker/coordinator runs.
6. Review handoffs before accepting, retrying, or asking the user.
7. Regenerate the dashboard after meaningful state changes.

Create a worker task:

```bash
python .codex-collab/collab.py new-task --owner worker-a --title "Task title" --goal "Concrete outcome"
```

Dry-run one worker:

```bash
python .codex-collab/collab.py start-worker --worker worker-a --dry-run --once
```

Dry-run one coordinator queue pass:

```bash
python .codex-collab/collab.py run-coordinator --dry-run --once
```

Review handoffs:

```bash
python .codex-collab/collab.py review --include-failed --include-human
```

## Live Mode

Before live runs, configure `.codex-collab/config.json` with the coordinator session and any worker sessions.

Minimum shape:

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

Check live readiness:

```bash
python .codex-collab/collab.py doctor --live
```

Live Codex launch is cross-platform: the runner resolves `codex` with `shutil.which`, routes Windows `.cmd` / `.bat` shims through `cmd.exe`, sends prompts through stdin, and decodes captured output as UTF-8 with replacement.

Run live worker/coordinator loops:

```bash
python .codex-collab/collab.py start-worker --worker worker-a
python .codex-collab/collab.py run-coordinator
```

## Rules That Keep It Sane

- Treat `tasks.json` as truth. Do not infer task state from Markdown.
- Treat `coordinator_queue.json` as truth for coordinator wakeups.
- Treat `dashboard.md` as generated output, not an input database.
- Use `repair-queue` when a task needs coordinator attention but no queue event exists.
- Use `approve <task-id>` before high-risk tasks run.
- Do not promise automatic git conflict resolution.
- Do not claim non-Codex Agent runtimes work without an adapter.

Useful commands:

```bash
python .codex-collab/collab.py approve <task-id>
python .codex-collab/collab.py repair-queue
python .codex-collab/collab.py status
python .codex-collab/collab.py clean --runs --state --reset-tasks --queue --force
```

## When To Read References

- Read `references/usage.md` when onboarding someone, explaining commands, or setting up a project.
- Read `references/design.md` when reviewing queue semantics, concurrency, recovery, or long-running reliability.
- Read `README.md` when explaining why the skill exists, how it differs from subagents, or how to present it publicly.

## Product Files

The reusable runner is:

```text
scripts/collab.py
```

Install that script into a project's `.codex-collab/` directory. Edit files inside the skill only when improving Codex Collab itself.
