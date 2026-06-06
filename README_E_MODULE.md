# E 模块运行说明

E 模块新增了统一后端 API 和 React 前端，用于把 A/B/C/D 组合成可演示产品。

## 后端

安装依赖：

```powershell
pip install -r requirements.txt
```

启动：

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

验收：

```powershell
python scripts\verify_e_module.py
python scripts\verify_e2e.py
python scripts\verify_knowledge.py
python scripts\run_harness.py
```

## 两种运行模式

### Demo 模式（无需真实账号）

前端默认开启“演示模式”，使用 `storage/demo_*.jsonl` 回退数据，隐藏旧学期和低优先级资料。

```powershell
python scripts\dev_start.ps1
```

### 真实账号模式

1. 在设置页保存学堂/邮箱/LLM 配置，或运行 `POST /api/settings/bootstrap` 从本地 txt 导入。
2. 一键流水线：

```powershell
.\scripts\full_pipeline.ps1 -StartServer
```

或分步：

```powershell
python main.py --channel all --allow-network --output storage/collector.jsonl
python scripts\export_attachments.py --source learn --jsonl storage/collector.jsonl
python scripts\parse_materials.py --incremental --records-jsonl storage/collector.jsonl
python -c "from backend.app.adapters.module_c import ModuleCAdapter; ModuleCAdapter().rebuild(force=True)"
```

D 模块为**真实 LLM**，需配置 `LLM_D_API_KEY`。无 Key / 无余额时仅阻塞问答/总结/作业页，不影响 Dashboard、任务中心、资料页。

## 前端

```powershell
cd frontend
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

## Demo 数据

后端默认读取真实运行产物：

- `storage/collector.jsonl`
- `storage/learn.jsonl`
- `storage/mail.jsonl`
- `storage/jwch.jsonl`
- `storage/material_chunks.jsonl`

如果暂时没有真实产物，可以用仓库内 Demo 文件临时指定：

```powershell
$env:COLLECTOR_JSONL="storage/demo_collector.jsonl"
$env:MATERIAL_CHUNKS_JSONL="storage/demo_material_chunks.jsonl"
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

首次启动时 C 模块会自动从 `MATERIAL_CHUNKS_JSONL` 构建知识库索引，索引文件保存在 `storage/knowledge_index/`。

如需要指定知识库索引路径，可设置环境变量 `KNOWLEDGE_INDEX_DIR`（默认 `storage/knowledge_index`）。

## 关键文档

- `CONTRIBUTING.md`：协作规范。
- `docs/e_module_architecture.md`：E 模块架构。
- `docs/interface_contracts.md`：A/B/C/D/E 接口契约。
- `docs/integration_status.md`：当前接入状态。
- `docs/demo_script.md`：答辩演示路线。
