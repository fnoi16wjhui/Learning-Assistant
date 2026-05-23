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
```

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

## 关键文档

- `CONTRIBUTING.md`：协作规范。
- `docs/e_module_architecture.md`：E 模块架构。
- `docs/interface_contracts.md`：A/B/C/D/E 接口契约。
- `docs/integration_status.md`：当前接入状态。
- `docs/demo_script.md`：答辩演示路线。
