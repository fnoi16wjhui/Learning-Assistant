# 模块接口契约

本文档定义 E 模块集成 A/B/C/D 的第一版稳定接口。已有接口直接接入；未完成接口由 E 先按本文档提供 Mock，后续由对应模块替换真实实现。

## 接口状态表

| 模块 | 当前状态 | E 的处理方式 | 后续补充内容 |
| --- | --- | --- | --- |
| A | 已有采集 CLI、Pydantic 模型、JSONL 输出 | 读取 `CourseTask`、`ScheduleItem`、同步状态并适配为前端 API | 稳定任务中心结构，补同步状态与错误码 |
| B | 已有资料解析脚本、`MaterialChunk` 输出 | 读取资料分块、解析状态、失败原因并适配为前端 API | 稳定标准化资料输出，补解析失败原因 |
| C | 已有 `src/knowledge/` 知识库核心、关键词/向量/混合检索 | 通过 `ModuleCAdapter` 读取真实知识库索引，返回检索结果 | 向量模型优化、增量更新、检索评测调优 |
| D | 未完成或不稳定 | E 提供问答、总结、作业助手 Mock | 实现问答、引用溯源、总结、作业助手 |

## 通用响应

成功响应直接返回业务对象。错误响应统一为：

```json
{
  "code": "MODULE_UNAVAILABLE",
  "message": "Module C retrieval service is not ready.",
  "source_module": "C",
  "detail": {}
}
```

分页列表使用：

```json
{
  "items": [],
  "total": 0,
  "source_module": "A",
  "status": "ready"
}
```

## E 统一 API

### `GET /api/health`

返回平台与各模块状态。

```json
{
  "status": "ok",
  "modules": {
    "A": "ready",
    "B": "ready",
    "C": "ready",
    "D": "mock",
    "E": "ready"
  }
}
```

### `GET /api/dashboard`

聚合 Dashboard 所需数据。

来源：
- A：任务、课表、同步状态。
- B：资料解析状态。
- C：知识库状态。
- D：智能应用状态。

```json
{
  "stats": {
    "task_count": 12,
    "schedule_count": 3,
    "material_count": 8,
    "pending_homework_count": 2
  },
  "recent_tasks": [],
  "sync_status": [],
  "material_status": {
    "total": 8,
    "parsed": 6,
    "failed": 1,
    "status": "ready"
  },
  "knowledge_status": {
    "status": "ready",
    "indexed_chunks": 12
  }
}
```

### `GET /api/tasks`

返回 A 模块任务中心数据。支持 `course_name`、`task_type`、`limit` 查询参数。

最小字段：

```json
{
  "raw_id": "learn_homework_1",
  "source": "learn",
  "course_name": "课程名",
  "title": "作业标题",
  "content": "作业要求",
  "task_type": "homework",
  "ddl": "2026-05-30T23:59:00+08:00",
  "attachments": [],
  "source_module": "A"
}
```

### `GET /api/schedules`

返回 A 模块课表/考试数据。

```json
{
  "raw_id": "jwch_schedule_1",
  "source": "jwch",
  "course_name": "课程名",
  "title": "课程安排",
  "schedule_type": "class",
  "starts_at": "2026-05-24T08:00:00+08:00",
  "ends_at": "2026-05-24T09:35:00+08:00",
  "location": "教学楼",
  "teacher": "教师",
  "source_module": "A"
}
```

### `POST /api/sync/run`

触发 A 模块同步。Demo 阶段可以只返回计划执行状态，不强制联网。

请求：

```json
{
  "channel": "all",
  "allow_network": false
}
```

响应：

```json
{
  "status": "queued",
  "message": "Sync request accepted.",
  "source_module": "A"
}
```

### `GET /api/sync/status`

返回 A 模块同步状态。

```json
{
  "items": [
    {
      "channel": "learn",
      "status": "ready",
      "last_synced_at": null,
      "message": "Read from storage/collector.jsonl"
    }
  ],
  "source_module": "A"
}
```

### `GET /api/materials`

返回 B 模块标准化资料输出。

```json
{
  "source_file": "storage/attachments/demo.pdf",
  "file_hash": "sha256",
  "material_type": "pdf",
  "course_name": "课程名",
  "title": "资料标题",
  "page": 1,
  "chunk_index": 0,
  "text": "资料片段",
  "metadata": {},
  "source_module": "B"
}
```

### `POST /api/materials/upload`

Demo 阶段接收上传占位请求，真实解析由 B 模块实现。

响应：

```json
{
  "status": "accepted",
  "message": "Upload endpoint is reserved for B module parser integration.",
  "source_module": "B"
}
```

### `GET /api/materials/parse-status`

返回 B 模块解析状态。

```json
{
  "total": 8,
  "parsed": 6,
  "failed": 1,
  "items": [],
  "source_module": "B"
}
```

### `GET /api/knowledge/status`

返回 C 模块知识库状态。

```json
{
  "status": "ready",
  "indexed_chunks": 12,
  "message": "Loaded index with 12 chunks.",
  "index_types": ["keyword", "vector", "hybrid"],
  "filters": ["course_name", "material_type"],
  "source_module": "C"
}
```

### `POST /api/retrieval/search`

C 模块检索接口。

请求：

```json
{
  "query": "课程重点是什么？",
  "course_name": "课程名",
  "top_k": 5,
  "mode": "hybrid"
}
```

响应：

```json
{
  "query": "课程重点是什么？",
  "items": [
    {
      "chunk_id": "chunk_abc123def456",
      "title": "课程导论资料",
      "course_name": "课程名",
      "text": "这里是知识库检索返回的真实结果片段。",
      "score": 0.95,
      "citation": "storage/attachments/demo.pdf"
    }
  ],
  "source_module": "C",
  "status": "mock"
}
```

### `POST /api/qa`

D 模块课程问答接口。

请求：

```json
{
  "question": "这门课的作业要求是什么？",
  "course_name": "课程名"
}
```

响应：

```json
{
  "answer": "这是 Mock 回答，真实实现由 D 模块提供。",
  "citations": [],
  "source_module": "D",
  "status": "mock"
}
```

### `POST /api/summaries`

D 模块总结接口。

请求：

```json
{
  "course_name": "课程名",
  "material_id": "optional",
  "topic": "本周内容"
}
```

### `POST /api/homework-assistant`

D 模块作业助手接口。

请求：

```json
{
  "task_id": "learn_homework_1",
  "question": "帮我分析这次作业思路"
}
```

响应：

```json
{
  "task_id": "learn_homework_1",
  "outline": ["理解题目", "召回相关知识", "形成解题步骤"],
  "draft": "这是 Mock 草稿，真实实现由 D 模块提供。",
  "citations": [],
  "source_module": "D",
  "status": "mock"
}
```
