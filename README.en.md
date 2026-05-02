# ToT Skills

[中文](README.md) · English

## 🧰 Practical skills for general-purpose Agents

ToT Skills is a collection of Agent skills for real work. Its goal is to package reusable workflows, tools, and constraints into local capabilities that general-purpose Agents can learn, install, and operate.

The current implementation and validation are Codex-first. Other Agents can port the protocol, but they need their own launch, resume, and permission adapters.

The skill currently published in this repository is **Codex Collab**. It lets one main Codex act as a coordinator, decompose complex tasks, dispatch persistent worker sessions, and collect results back through a queue.

---

## 🚀 Ask Codex To Install

Paste this into Codex:

```text
Please install this skill: https://github.com/ZelongTAN/tot-skills/tree/main/skills/codex-collab
```

If you want the whole collection:

```text
Please install the skills from this repository: https://github.com/ZelongTAN/tot-skills
```

---

## ✨ Current Skill

| Skill | What it does | Use it when |
|---|---|---|
| [Codex Collab](skills/codex-collab/) | Lets one main Codex become a coordinator that decomposes complex work and dispatches persistent worker sessions | Long tasks, complex coding, or work that benefits from research / implementation / testing / review split across sessions |

Codex Collab docs are split into three layers:

- [Human-facing product page](skills/codex-collab/README.md): pain points, Before/After, highlights, install entry, and mechanism diagrams.
- [Agent runtime entry](skills/codex-collab/SKILL.md): concise operating instructions loaded after the skill triggers.
- [Deep references](skills/codex-collab/references/): `usage.md` is the operating manual, and `design.md` covers design and reliability.

Note: Codex Collab's full platform boundary lives in its own docs. Codex is supported today; other Agent runtimes mainly need launch, resume, permission, and session-management adapters.

---

## License

MIT. See [LICENSE](LICENSE).
