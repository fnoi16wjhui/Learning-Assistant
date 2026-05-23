# 集成状态

本文档用于记录 E 模块对 A/B/C/D 的接入状态。没有完成的接口先保留 Mock，后续按 `docs/interface_contracts.md` 替换真实实现。

## 当前状态

| 模块 | 状态 | E 当前接入位置 | 说明 |
| --- | --- | --- | --- |
| A | ready/missing | `backend/app/adapters/module_a.py` | 读取 `storage/collector.jsonl`、`storage/learn.jsonl`、`storage/mail.jsonl`、`storage/jwch.jsonl` 中的任务与课表记录 |
| B | ready/missing | `backend/app/adapters/module_b.py` | 读取 `storage/material_chunks.jsonl` 中的标准化资料分块 |
| C | mock | `backend/app/adapters/module_c.py` | 已定义知识库状态与检索接口，等待 C 替换真实知识库实现 |
| D | mock | `backend/app/adapters/module_d.py` | 已定义问答、总结、作业助手接口，等待 D 替换真实智能应用实现 |
| E | ready | `backend/app/main.py`、`frontend/src/App.tsx` | 提供统一 API 和前端 Demo 页面 |

## 成员补充格式

```text
接口名：
负责人：
当前状态：ready / mock / in_progress / blocked
调用方式：函数 / 脚本 / HTTP API / JSONL
请求示例：
响应示例：
错误响应：
E 当前 Mock 文件/路由：
验收方式：
所属页面：Dashboard / 任务中心 / 资料页 / 问答页 / 总结页 / 作业页
```

## 替换 Mock 的步骤

1. 在对应 `backend/app/adapters/module_*.py` 中接入真实函数、脚本或 HTTP API。
2. 保持 `docs/interface_contracts.md` 中的 E 层响应字段稳定。
3. 如果真实模块字段不同，只在 adapter 中转换，不让前端直接依赖内部字段。
4. 更新本文档状态。
5. 运行后端健康检查和前端构建。
