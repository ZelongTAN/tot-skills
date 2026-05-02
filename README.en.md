# ToT Skills

[中文](README.md) · English

#### 🧰 Practical skills for real Agent work

ToT Skills is a collection of Agent skills for real workflows. Each skill should be installable by an Agent, usable without ceremony, and designed to turn a complex process into a reusable local workflow.

The point is not to make humans memorize commands. The point is to make Agents more capable: give Codex, Claude Code, OpenCode, OpenClaw, or another skill-aware Agent an install link, and let it wire the skill into its own workflow.

---

## 🚀 Ask Your Agent To Install

Paste this into your Agent:

```text
Please install the relevant skill from this repository: https://github.com/ZelongTAN/tot-skills
```

To install only Codex Collab:

```text
Please install this skill: https://github.com/ZelongTAN/tot-skills/tree/main/skills/codex-collab
```

---

## Skills

| Skill | What it does | Use it when |
|---|---|---|
| [Codex Collab](skills/codex-collab/) | Lets one main Codex become a coordinator that decomposes complex work and dispatches persistent worker sessions | Long tasks, complex coding, or work that benefits from research / implementation / testing / review split across sessions |

---

## Codex Collab In One Minute

Codex Collab is not about humans manually operating a multi-agent framework. It gives the main Codex a collaboration skill: you still talk to one main Codex; that Codex can decompose the goal, assign subtasks to persistent worker sessions, collect handoffs, review them in order, and report the decision back to you.

**Before: one Codex does everything**

- You give one Codex a complex task.
- It understands the goal, researches, codes, tests, and reviews by itself.
- As the task gets longer, context gets heavy, responsibilities mix together, and quality can drop.

**After: one main Codex coordinates persistent workers**

- 👤 the human talks to one main Codex
- 🧠 the main Codex decomposes, dispatches, maintains the dashboard, and makes final decisions
- 🛠️ worker sessions handle implementation, research, tests, or review
- 🔁 worker results enter a queue for coordinator review
- 📦 state lives in local JSON and run artifacts, so long work can recover

Read more: [skills/codex-collab](skills/codex-collab/)

---

## Cross-Platform Notes

- Windows / macOS / Linux are supported.
- Codex, Claude Code, OpenCode, OpenClaw, and similar skill-aware Agents can use these skills.
- Claude can run this too; using it as long-lived worker fuel is a bit like delivering groceries in a sports car. Technically fine. The wallet may object first.

---

## License

MIT. See [LICENSE](LICENSE).
