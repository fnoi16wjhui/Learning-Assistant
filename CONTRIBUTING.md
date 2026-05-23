# 协作规范

本仓库按 A/B/C/D/E 模块协作。E 模块负责产品平台、前端、统一接口和总集成；A/B/C/D 保持各自核心逻辑边界，不把内部实现耦合到前端。

## 分支

- `main` 保持可演示、可启动。
- 各成员从 `main` 拉分支开发：`feature/a-*`、`feature/b-*`、`feature/c-*`、`feature/d-*`、`feature/e-*`。
- 修复演示问题使用 `fix/*`，文档更新使用 `docs/*`。

## 提交信息

使用简短英文前缀，便于快速看出改动目的：

```text
feat: add task api
fix: handle empty material chunks
docs: update module c contract
test: cover dashboard endpoint
chore: update demo data
```

## PR 与合并

- 每个 PR 只改一个明确主题，避免把多个模块混在一起。
- PR 描述需要写清楚：改了什么、如何运行、影响哪些接口。
- 涉及接口字段变化时，必须同步更新 `docs/interface_contracts.md`。
- 不提交 `.env`、账号密码、cookie、token、真实个人数据和运行时数据库。

## 接口变更规则

E 模块的前端只调用 `backend` 提供的统一 API。A/B/C/D 可以用脚本、函数、本地文件或 HTTP 服务交付能力，但都需要通过 E 的适配器接入。

接口字段一旦进入 `docs/interface_contracts.md`，默认保持稳定。确实需要改动时：

1. 先在 PR 中说明字段变化原因。
2. 更新请求/响应样例。
3. 保留 E 的 Mock 或适配逻辑可运行。
4. 确认前端页面没有被破坏。

## 联调要求

每个模块至少提供：

- 调用方式：函数、脚本、HTTP API 或 JSONL 文件。
- 请求样例和响应样例。
- 错误响应格式。
- 最小 Demo 数据。
- 当前状态：`ready`、`mock`、`in_progress` 或 `blocked`。

缺失接口先由 E 在 `backend/app/adapters/` 中提供 Mock，占位接口不代表最终实现归 E。
