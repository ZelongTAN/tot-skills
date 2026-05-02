# ToT Skills

中文 · [English](README.en.md)

## 🧰 给通用型 Agent 用的实用 skill 集合

ToT Skills 是一组面向真实工作的 Agent skills。它的目标是把可复用的协作流程、工具和约束，整理成通用型 Agent 可以学习、安装和操作的本地能力。

当前实现和验证优先围绕 Codex。其他 Agent 可以参考这套协议迁移，但需要替换启动、resume 和权限参数等适配层。

当前仓库公开发布的 skill 是 **Codex Collab**。它让一个主 Codex 学会当 coordinator，自己拆解复杂任务，调度持久 worker 会话，并把结果按队列收回审查。

---

## 🚀 直接让 Codex 安装

把这句话复制给 Codex：

```text
请帮我安装这个 skill：https://github.com/ZelongTAN/tot-skills/tree/main/skills/codex-collab
```

如果你想安装整个集合：

```text
请帮我安装这个仓库里的 skills：https://github.com/ZelongTAN/tot-skills
```

---

## ✨ 当前 Skill

| Skill | 它做什么 | 适合什么时候用 |
|---|---|---|
| [Codex Collab](skills/codex-collab/) | 让一个主 Codex 成为 coordinator，拆解复杂任务并调度持久 worker 会话 | 长任务、复杂代码任务、需要调研 / 实现 / 测试 / 审查分工的任务 |

Codex Collab 的文档分成三层：

- [人看的产品页](skills/codex-collab/README.md)：痛点、Before/After、亮点、安装入口和机制图。
- [Agent 运行时入口](skills/codex-collab/SKILL.md)：Codex 触发 skill 后读取的精简操作说明。
- [深入文档](skills/codex-collab/references/)：`usage.md` 是操作手册，`design.md` 是设计和可靠性说明。

说明：Codex Collab 的完整平台边界在自己的文档里。当前仓库已经适配 Codex；其他 Agent 的迁移重点是启动、resume、权限和会话管理适配层。

---

## License

MIT. See [LICENSE](LICENSE).
