# C 模块：知识库与检索（Knowledge Base & Retrieval）

## 概述

C 模块负责将 B 模块输出的标准化课程资料构建为可检索的知识库，
对外提供关键词检索、向量检索和混合检索能力。

## 架构

```
src/knowledge/                       # C 模块核心代码
├── __init__.py                      # 公开接口
├── models.py                        # 数据模型
├── keyword_index.py                 # 关键词索引（jieba + TF-IDF）
├── vector_index.py                  # 向量索引（可选，sentence-transformers）
├── indexer.py                       # 索引构建器（从 JSONL 构建索引）
├── retriever.py                     # 检索器（RRF 混合检索 + 元数据过滤）
└── knowledge_base.py               # 知识库管理器（统一入口）

backend/app/adapters/module_c.py     # E 模块适配器
```

## 依赖

| 依赖 | 版本 | 必需 | 说明 |
|------|------|------|------|
| jieba | >=0.42.1 | ✅ | 中文分词，关键词检索核心 |
| numpy | >=1.24 | ✅ | 向量计算 |
| sentence-transformers | >=2.2 | ❌ 可选 | 向量检索模型 |

## 快速验证

```powershell
python scripts\verify_knowledge.py
```

## 检索模式

| 模式 | 说明 | 前提条件 |
|------|------|---------|
| keyword | jieba 分词 + TF-IDF 倒排索引 | 安装 jieba（默认） |
| vector | 余弦相似度向量检索 | 安装 sentence-transformers |
| hybrid | RRF 混合检索（关键词 + 向量融合） | 安装 sentence-transformers |

## 数据存储

| 路径 | 说明 |
|------|------|
| storage/material_chunks.jsonl | B 模块输出（输入） |
| storage/demo_material_chunks.jsonl | Demo 示例数据 |
| storage/knowledge_index/ | 知识库索引持久化 |

## API 接口

- GET /api/knowledge/status — 知识库状态
- POST /api/retrieval/search — 知识库检索

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| MATERIAL_CHUNKS_JSONL | storage/material_chunks.jsonl | B 模块输出路径 |
| KNOWLEDGE_INDEX_DIR | storage/knowledge_index | 索引持久化路径 |

## 设计要点

- 关键词检索始终可用，仅依赖 jieba
- 向量检索为可选增强，自动降级
- 首次 API 访问时自动构建索引（build_if_needed）
- 混合检索使用 RRF（k=60），无需分数尺度对齐
