# Codex Collab Usage

Version: `0.1.3`

Codex-first local runner for a main Codex coordinator and one or more persistent Codex worker sessions.

The key mental model is persistent worker identity, not generic parallelism. A worker is a continuing session that can be resumed, questioned again, and asked for a second pass. The coordinator's job is to assign scope, preserve context quality, review handoffs, and decide whether the same worker or a different worker should take the next slice.

Default rule:

- Reuse the same worker for the same thread of work.
- Start a different worker only when you are intentionally changing role, scope, or ownership.

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
  reviews/                 per-review coordinator prompts and logs
  state/                   worker heartbeat, stop files, locks
```

The active queue of work is `tasks.json`; per-run evidence lives under `runs/`.

Artifact naming is intentionally fixed:

- worker execution lives under `runs/<run-id>/`
- coordinator review execution lives under the queue event's `reviewPath`
- file names stay stable so humans and Agents can inspect them without guessing

Worker run artifacts:

- `task.json`
- `worker-prompt.md`
- `handoff.md`
- `run.log`
- `last-message.md`

Coordinator review artifacts:

- `event.json`
- `coordinator-prompt.md`
- `run.log`
- `last-message.md`

Queue events also carry direct pointers:

- `reviewPath`
- `eventPath`
- `promptPath`
- `runLogPath`
- `lastMessagePath`

That means a queue row is not just a notification. It is also a navigable entry into the review folder for that event.

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
python .codex-collab/collab.py start-worker --worker worker-a --exercise-flow --once
python .codex-collab/collab.py run-coordinator --exercise-flow --once
python .codex-collab/collab.py review
python .codex-collab/collab.py dashboard
```

`--dry-run` is read-only. It previews the next eligible task or coordinator queue event and does not write `tasks.json`, `coordinator_queue.json`, `runs/`, or `state/`.

`--exercise-flow` is the mutating local rehearsal mode. It claims a task, writes fake run artifacts, enqueues coordinator attention, and processes the queue without invoking real Codex. Use it on disposable smoke tasks, not real work.

## Writing Better Tasks

The easiest way to improve worker quality is not a heavier framework. It is better task briefs.

That matters even more here because tasks are not just one-shot prompts. A good task brief helps the same worker identity survive into retries, follow-up questions, and second-pass work without losing the thread.

Use these fields on purpose:

- `title`: short label for the board
- `goal`: one-sentence success condition
- `context`: why the task exists, what is already known, and which files to read first
- `boundary`: what not to change, ownership limits, or explicit non-goals
- `deliverable`: what the worker should hand back
- `validation`: the checks to run, or when a no-command explanation is acceptable

Good `context` usually answers three things in one paragraph:

- what larger task this supports
- which files or folders to inspect first
- any important local rule the worker should honor

Keep it light. The runner already tells the worker where the handoff goes and which runtime files are authoritative. The coordinator does not need to restate that in every task.

## When To Reuse A Worker

Prefer the same worker when:

- the handoff is mostly right but needs one more patch
- review feedback sends the same implementation back for revision
- the same research thread needs one more question answered
- the same code area needs a second pass or a narrower retry

Prefer a different worker when:

- the task moves into a different file cluster or subsystem
- you want an intentionally independent reviewer
- the role changes from implementation to research, or from research to execution
- the original worker context is no longer the right home for the next step

Simple mental shortcut:

- same thread, same worker
- new thread, new worker

Implementation example:

```bash
python .codex-collab/collab.py new-task --owner worker-a --title "Improve task brief guidance" --goal "Add lightweight coordinator guidance for writing clearer worker tasks." --context "Background: worker quality drops when the task lacks background, file pointers, or validation expectations. Read first: skills/codex-collab/SKILL.md, skills/codex-collab/references/usage.md, and skills/codex-collab/scripts/collab.py help text around new-task. Keep the change lightweight." --boundary "Do not add a new templating engine or new JSON schema fields." --deliverable "Update the docs and CLI guidance so coordinator Codex has examples to imitate." --validation "Run python -m py_compile skills/codex-collab/scripts/collab.py." --validation "Run git diff --check."
```

Research example:

```bash
python .codex-collab/collab.py new-task --owner worker-b --title "Research worker briefing failure modes" --goal "Summarize the most common ways coordinator-written tasks become ambiguous." --context "This is read-only. Read first: skills/codex-collab/SKILL.md and skills/codex-collab/references/usage.md. Focus on missing background, weak file pointers, unclear deliverables, and absent validation instructions." --boundary "Do not edit files in this task." --deliverable "Return a short handoff with findings and improved example wording." --validation "No command validation required; state that this was a read-only reasoning task."
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
      "model": "gpt-5.4",
      "reasoningEffort": "xhigh",
      "approvalPolicy": "on-request",
      "search": true,
      "sandbox": "workspace-write"
    }
  },
  "coordinator": {
    "sessionId": "main-coordinator-session-id",
    "model": "gpt-5.4",
    "reasoningEffort": "xhigh",
    "approvalPolicy": "on-request",
    "search": true,
    "sandbox": "workspace-write"
  }
}
```

`model` and `reasoningEffort` are optional. If omitted, Codex uses the user's normal CLI defaults. Common reasoning effort values are `minimal`, `low`, `medium`, `high`, and `xhigh`.

For live resume use, treat `cwd`, `sandbox`, `approvalPolicy`, and `search` as part of the worker's runtime contract, not just session metadata. A persistent session keeps context, but the runner still needs to resume it with the intended working root and execution capabilities.

If a resumed worker says it is read-only or cannot search, fix the worker config first: keep `useResume=true`, set `cwd` to the project or worktree, and make sure `sandbox`, `approvalPolicy`, and `search` match the intended live behavior.

Live worker:

```bash
python .codex-collab/collab.py start-worker --worker worker-a
```

Override model and reasoning effort for one worker loop:

```bash
python .codex-collab/collab.py start-worker --worker worker-a --model gpt-5.4 --reasoning-effort xhigh
```

Live coordinator:

```bash
python .codex-collab/collab.py run-coordinator
```

Override model and reasoning effort for one coordinator loop:

```bash
python .codex-collab/collab.py run-coordinator --model gpt-5.4 --reasoning-effort xhigh
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

Preview the next queue event without changing it:

```bash
python .codex-collab/collab.py run-coordinator --dry-run --once
```

Exercise the queue state machine without calling Codex:

```bash
python .codex-collab/collab.py run-coordinator --exercise-flow --once
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
