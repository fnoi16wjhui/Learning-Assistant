# Docs Tasks

## 目标

为信息获取 / 数据采集团队提供统一的架构说明、协作边界和验收 harness，使网络学堂、邮箱、教务/课表等子任务能在同一四层流水线下并行推进。

## 边界

`docs/` 只负责说明“为什么这样设计”和“如何协作”，不保存真实凭证，不包含可用 Cookie，不替代代码实现。

允许：

- 解释 Adapters、Parsers、Models、Pipeline 的职责。
- 记录 Storage、Logs、`main.py` 调度和下游交付策略。
- 记录子代理 Plan / Implementation / Reflection harness。
- 记录后续风险和验收方式。

禁止：

- 放入真实学号、密码、邮箱授权码、Cookie、Token 或 API Key。
- 在文档中要求 Parser 直接访问网络或要求 Adapter 直接构造业务模型。
- 把未讨论过的核心 Schema 变更伪装成既定实现。

## 输入

- `Rules.md`
- `architect.md`
- 现有代码目录结构和任务拆分。

## 输出

- `docs/information_acquisition_architecture.md`
- `docs/tasks.md`

## 任务列表

- [x] 明确信息获取模块的目标和边界。
- [x] 说明四层流水线的职责、禁止事项和分层原因。
- [x] 说明 Storage / Logs 的敏感信息保护策略。
- [x] 说明 `main.py` 默认 dry-run 的原因。
- [x] 说明下游交付形式：SQLite、JSONL、未来 Agent API。
- [x] 写入子代理 harness：Plan、Implementation、Reflection。
- [ ] 在真实接口接入后补充运行示例和故障排查流程。

## 验收 harness

- `python scripts/run_harness.py` 应确认 `docs/tasks.md` 存在。
- 文档中的层边界必须与 `Rules.md` 保持一致。
- 文档不得包含真实敏感值。
- 如果后续修改核心 Schema，必须先更新文档并让团队确认。

## 风险

- 文档过时会导致子代理按旧边界实现，后续需要把文档更新纳入每次架构变更流程。
- 真实接口的登录流程尚未稳定，当前文档只能约束边界，不能代替接口分析。
- 下游交付协议仍可能变化，文档需要保留低耦合描述。
