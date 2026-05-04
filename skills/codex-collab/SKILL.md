---
name: codex-collab
description: Set up and operate Codex Collab, a Codex-first JSON coordination skill for complex work. Use when the user wants one main Codex to act as coordinator, split a goal into worker tasks, dispatch persistent Codex worker sessions, collect handoffs through a durable queue, maintain a dashboard, review results, or install/package the local runner. The protocol can be ported to other Agent runtimes, but this implementation is built around Codex CLI/session resume.
---

# Codex Collab

Give one main Codex a coordinator desk.

This skill is not mainly about parallel search. Its core is persistent worker identity.

Each worker is meant to be a continuing session with its own scope, context, and follow-up history. A worker can be resumed, questioned again, asked for a second pass, or given a narrower retry. The main Codex should act as the coordinator: give enough background, specify what to read, specify what to write back, review the handoff, and then decide the next round.

Default rule of thumb:

- Reuse the same worker when the next step is a follow-up on the same workstream: patch a gap, answer review feedback, extend the same implementation, or run a second pass.
- Open a different worker when the workstream truly changes: different files, different role, different expertise, or intentionally independent review.

The user should keep talking to one main Codex. That coordinator Codex uses a project-local `.codex-collab/` workspace to split work, assign persistent worker Codex sessions, collect handoffs, review results, and keep the dashboard current. The human should not become the message bus.

Codex Collab is JSON-first:

- `tasks.json` is the task source of truth.
- `coordinator_queue.json` is the coordinator wakeup source of truth.
- `runs/` stores worker evidence: task snapshots, logs, and handoffs.
- queue events carry direct pointers into `reviews/` so the coordinator can open the review prompt, event snapshot, log, and last message without guessing paths.
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
2. Treat workers as persistent specialists you may resume later, not as disposable parallel searches.
3. Break the goal into small, low-coupling worker tasks.
4. Give each task enough background, reading pointers, and handoff expectations to stand on its own.
5. Prefer reusing or re-briefing the same worker identity when a second pass belongs to the same thread of work.
6. Keep code-changing workers in separate git worktrees when possible.
7. Run read-only dry-run previews before live worker/coordinator runs.
8. Review handoffs before accepting, retrying, or asking the user.
9. Regenerate the dashboard after meaningful state changes.

Create a worker task:

```bash
python .codex-collab/collab.py new-task --owner worker-a --title "Task title" --goal "Concrete outcome"
```

## Task Brief Pattern

When the coordinator creates a worker task, keep it light but specific.

Write the task as if this worker may be resumed later for a second round. The brief should preserve enough identity and context that the same worker can be asked follow-up questions instead of being replaced by a brand-new branch.

If a worker returns a useful but incomplete handoff, do not reflexively fan the same topic out into new parallel branches. First ask whether the same worker should patch the gap and keep continuity.

What the runner already injects automatically:

- the source-of-truth paths such as `tasks.json`
- the worker's task snapshot under `runs/<run-id>/task.json`
- the required handoff path under `runs/<run-id>/handoff.md`
- the expected handoff sections and allowed status values

What the coordinator still needs to say clearly:

- background: why this task exists and what larger goal it supports
- read first: the few project files or folders the worker should inspect first
- boundary: what not to change, or what area the worker owns
- deliverable: what a good result should contain
- validation: which checks to run, or when explanation is acceptable

Map that onto the CLI fields:

- `--goal`: one-sentence outcome
- `--context`: background, known facts, and the first files to read
- `--boundary`: constraints and non-goals
- `--deliverable`: what should appear in the handoff or code result
- `--validation`: required checks

Do not waste task space repeating the runtime plumbing. The runner already tells the worker where the handoff goes and which Collab files are truth sources.

Implementation-style example:

```bash
python .codex-collab/collab.py new-task ^
  --owner worker-a ^
  --title "Tighten queue retry handling" ^
  --goal "Make stale queue events resolve cleanly without noisy warnings." ^
  --context "Background: users are seeing stale queue warnings after retries. Read first: skills/codex-collab/scripts/collab.py and skills/codex-collab/references/design.md. Keep the change scoped to queue validation and reconciliation." ^
  --boundary "Do not redesign the queue model or add new storage." ^
  --deliverable "Code change plus a short handoff that explains the new stale-event behavior." ^
  --validation "Run python -m py_compile skills/codex-collab/scripts/collab.py." ^
  --validation "Run python skills/codex-collab/scripts/collab.py validate."
```

Read-only research example:

```bash
python .codex-collab/collab.py new-task ^
  --owner worker-b ^
  --title "Research lighter task briefing patterns" ^
  --goal "Produce a short recommendation for how coordinator prompts should specify context and file-reading order." ^
  --context "This is a read-only research task. Read first: skills/codex-collab/SKILL.md and skills/codex-collab/references/usage.md. Compare the current task-writing guidance with common failure modes: vague background, missing file pointers, missing validation expectations." ^
  --boundary "Do not change code or docs in this task." ^
  --deliverable "Write a concise handoff with 3-5 recommendations and 1-2 example task phrasings." ^
  --validation "Validation is reasoning-only; note that no commands were run."
```

Preview one worker without mutating state:

```bash
python .codex-collab/collab.py start-worker --worker worker-a --dry-run --once
```

Preview one coordinator queue pass without mutating state:

```bash
python .codex-collab/collab.py run-coordinator --dry-run --once
```

Exercise the local state flow without invoking real Codex:

```bash
python .codex-collab/collab.py start-worker --worker worker-a --exercise-flow --once
python .codex-collab/collab.py run-coordinator --exercise-flow --once
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
      "model": "gpt-5.4",
      "reasoningEffort": "xhigh",
      "sandbox": "workspace-write"
    }
  },
  "coordinator": {
    "sessionId": "main-coordinator-session-id",
    "model": "gpt-5.4",
    "reasoningEffort": "xhigh"
  }
}
```

Check live readiness:

```bash
python .codex-collab/collab.py doctor --live
```

Live Codex launch is cross-platform: the runner resolves `codex` with `shutil.which`, routes Windows `.cmd` / `.bat` shims through `cmd.exe`, sends prompts through stdin, and decodes captured output as UTF-8 with replacement.

`model` and `reasoningEffort` are optional. The coordinator can also override them for a single loop with `--model gpt-5.4 --reasoning-effort xhigh`.

For persistent worker sessions, remember that `resume` is about continuity, not capability by itself. The live runner should still resume with the intended `cwd`, sandbox/approval posture, and optional search setting so the worker returns to the right execution environment instead of falling back to a weaker session default.

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
