# Demo 演示脚本

## 准备

安装 Python 依赖：

```powershell
pip install -r requirements.txt
```

安装前端依赖：

```powershell
cd frontend
npm install
```

启动方式：

```powershell
scripts\dev_start.ps1
```

也可以分开启动：

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
cd frontend
npm run dev
```

## 演示路线

1. 打开前端首页，展示 Dashboard。
2. 说明顶部模块状态：A/B 读取已有产物，C 知识库检索已就绪（关键词检索可用，向量检索可选），D 当前为 Mock，E 为统一平台。
3. 打开任务中心，展示 A 模块提供的公告、作业、附件和课表数据。
4. 打开资料页，展示 B 模块解析状态和资料分块，同时展示 C 模块知识库状态。
5. 打开问答页，输入课程问题，展示 D 模块问答接口与引用区域。
6. 打开总结页，生成资料总结。
7. 打开作业页，说明作业要求来自 A，知识片段来自 C，最终建议由 D 生成。
8. 打开设置页，展示统一 API 健康状态。

## 讲解重点

- E 不实现 A/B/C/D 的核心算法，而是负责统一接口、前端、状态展示、联调和部署。
- 已完成接口直接接入；未完成接口先用契约和 Mock 保证产品闭环。
- 后续替换 Mock 时只改 `backend/app/adapters/`，前端页面不需要跟着每个模块内部变化改动。
