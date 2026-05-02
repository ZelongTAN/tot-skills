# ToT Skills

中文 · [English](README.en.md)

#### 🧰 给 Agent 用的实用 skill 集合

ToT Skills 是一组面向真实工作的 Agent skills。每个 skill 都应该能被 Agent 直接安装、直接使用，并尽量把复杂流程变成可复用的本地工作方式。

这里的重点不是让人背命令，而是让 Agent 变得更会干活：你把安装链接丢给 Codex、Claude Code、OpenCode、OpenClaw 等支持 skill 的 Agent，它就能把对应 skill 装进自己的工作流里。

---

## 🚀 直接让 Agent 安装

把下面这句话复制给你的 Agent：

```text
请从这个仓库安装需要的 skill：https://github.com/ZelongTAN/tot-skills
```

如果你只想安装 Codex Collab：

```text
请帮我安装这个 skill：https://github.com/ZelongTAN/tot-skills/tree/main/skills/codex-collab
```

---

## Skills

| Skill | 作用 | 适合什么时候用 |
|---|---|---|
| [Codex Collab](skills/codex-collab/) | 让一个主 Codex 成为调度员，拆解复杂任务并调度持久 worker 会话 | 长任务、复杂代码任务、需要调研/实现/测试/审查分工时 |

---

## Codex Collab 简介

Codex Collab 不是让人手动管理一个“多 Agent 框架”。它给主 Codex 一个协作 skill：你仍然只和一个主 Codex 对接；主 Codex 可以自己拆任务、分配给持久 worker 会话、收集 handoff、排队审查，再把结论交还给你。

**Before：一个 Codex 硬做到底**

- 你把复杂任务交给一个 Codex。
- 它自己理解需求、查资料、写代码、跑测试、审查结果。
- 任务一长，上下文变重，职责混在一起，质量容易下滑。

**After：一个主 Codex 调度持久 worker**

- 👤 人只面对一个主 Codex
- 🧠 主 Codex 负责拆解、调度、维护 dashboard 和最终判断
- 🛠️ worker 会话负责实现、调研、测试、审查等子任务
- 🔁 worker 完成后进入队列，主 Codex 按顺序审查
- 📦 状态落在本地 JSON 和 run artifacts 里，适合长时间运行和恢复

查看更多：[skills/codex-collab](skills/codex-collab/)

---

## 跨平台说明

- Windows / macOS / Linux 都可以使用。
- Codex、Claude Code、OpenCode、OpenClaw 等支持 skill 的 Agent 都可以接入。
- Claude 当然也能用，只是长期拿它烧 worker 会话有点像开跑车送外卖：技术上没问题，钱包可能先抗议。

---

## License

MIT. See [LICENSE](LICENSE).
