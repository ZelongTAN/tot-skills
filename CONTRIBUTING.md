# Contributing

Thanks for improving ToT Skills.

## Skill Rules

- Put each skill under `skills/<skill-name>/`.
- Keep each skill self-contained with a required `SKILL.md`.
- Keep repository-level collection docs in the repository root.
- Use a skill-level `README.md` only when the skill needs a human-facing product page.
- Keep `SKILL.md` concise and action-oriented because it is loaded by Agents at runtime.
- Keep detailed operational notes in a skill's `references/` directory.
- Do not commit local runtime folders, session IDs, logs, or generated state.

## Local Checks

Run these before publishing changes:

```bash
python -m py_compile skills/codex-collab/scripts/collab.py
python path/to/quick_validate.py skills/codex-collab
```

For Codex Collab smoke testing:

```bash
python skills/codex-collab/scripts/collab.py install --target /tmp/codex-collab-smoke --dashboard
python /tmp/codex-collab-smoke/.codex-collab/collab.py doctor
python /tmp/codex-collab-smoke/.codex-collab/collab.py validate
python /tmp/codex-collab-smoke/.codex-collab/collab.py new-task --owner worker-a --title "Smoke test" --goal "Verify dry-run"
python /tmp/codex-collab-smoke/.codex-collab/collab.py start-worker --worker worker-a --dry-run --once
python /tmp/codex-collab-smoke/.codex-collab/collab.py run-coordinator --dry-run --once
python /tmp/codex-collab-smoke/.codex-collab/collab.py validate
```
