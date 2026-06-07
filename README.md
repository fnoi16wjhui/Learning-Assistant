# Learning Assistant Collector

这是 Course Agent 项目的信息获取 / 数据采集模块。当前模块负责从网络学堂、清华邮箱 IMAP、Info / 教务系统获取课程相关信息，并统一转换为下游可消费的 `CourseTask` / `ScheduleItem` JSONL 记录。

本仓库按 `Rules.md` 的四层边界组织：

- `src/adapters/`：只负责登录、会话、网络请求、原始内容获取。
- `src/parsers/`：只做纯解析，不访问网络、不读 `.env`、不写数据库。
- `src/models/`：统一 Pydantic v2 数据契约。
- `src/pipeline.py` / `main.py`：负责调度、去重、状态保存、JSONL 输出。

## 快速开始

在仓库根目录执行：

```powershell
Copy-Item config\.env.example .env
python scripts\run_harness.py
```

`.env` 是本地敏感配置文件，不要提交到 Git。当前加载器支持 UTF-8 和 GBK 编码；建议新文件使用 UTF-8。

安装依赖后，可以先做架构 harness：

```powershell
python scripts\run_harness.py
```

真实联网同步需要显式加 `--allow-network`：

```powershell
python main.py --channel learn --allow-network --output storage/learn.jsonl
python main.py --channel mail --allow-network --output storage/mail.jsonl
python main.py --channel jwch --allow-network --output storage/jwch.jsonl
python main.py --channel all --allow-network --output storage/collector.jsonl
```

## `.env` 配置

推荐从 `config/.env.example` 复制：

```env
# 网络学堂配置
LEARN_USERNAME=
LEARN_PASSWORD=
LEARN_BASE_URL=https://learn.tsinghua.edu.cn
LEARN_EXTRA_JSON={"login_url":"https://learn.tsinghua.edu.cn","username_field":"i_user","password_field":"i_pass"}

# 清华邮箱配置
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_BASE_URL=mails.tsinghua.edu.cn
MAIL_EXTRA_JSON={"imap_port":993,"use_ssl":true,"timeout_seconds":20}

# 教务 / Info 配置
# JWCH_USERNAME/JWCH_PASSWORD 可以留空；留空时会复用 LEARN_USERNAME/LEARN_PASSWORD。
JWCH_USERNAME=
JWCH_PASSWORD=
JWCH_BASE_URL=https://zhjw.cic.tsinghua.edu.cn
JWCH_EXAM_URL=https://zhjw.cic.tsinghua.edu.cn/jxmh.do?url=/jxmh.do&m=bks_ksSearch
JWCH_SCHEDULE_URL=https://zhjw.cic.tsinghua.edu.cn/portal3rd.do?url=/portal3rd.do&m=bks_yjkbSearch
# 可选。填写真实周一日期后，课表会映射到真实日期；留空时使用 1970-01-05 作为稳定占位周一。
JWCH_ANCHOR_MONDAY=
JWCH_EXTRA_JSON={"login_url":"https://info.tsinghua.edu.cn","username_field":"i_user","password_field":"i_pass","trust_path":"storage/learn_trust_device.json","exam_app_id":"81008AA5A89C20D5BDBBDF719D5F0A94","schedule_app_id":"287C0C6D90ABB364CD5FDF1495199962","timeout_seconds":20}

# D 模块 LLM 配置
# LLM_D_BASE_URL   — OpenAI 兼容 API 地址（不含 /v1/chat/completions 路径）
# LLM_D_API_KEY    — API 密钥（留空则不带 Authorization 头）
# LLM_D_MODEL      — 模型名称
# LLM_D_TIMEOUT    — HTTP 请求超时秒数
LLM_D_BASE_URL=https://llmapi.paratera.com
LLM_D_API_KEY=
LLM_D_MODEL=deepseek-chat
LLM_D_TIMEOUT=60
```

如果密码包含 `#`、空格、引号等特殊字符，建议用英文双引号包住整段值。

> **注意**：`.env.example` 在 D 模块集成后新增了 `LLM_D_BASE_URL` 和 `LLM_D_API_KEY` 等 LLM 配置项。如果你之前已经拷贝过 `.env`，请重新从 `config/.env.example` 复制一份，或手动补上上述 LLM 配置段，否则问答助手将无法正常工作。

## 为什么 Info / JWCH 可以不填 `JWCH_USERNAME`

你的 `.env` 中 Info / 教务登录使用 `LEARN_USERNAME` 和 `LEARN_PASSWORD` 是正常的。原因是清华统一身份认证通常使用同一套账号密码，代码在 `JwchAdapter._credential()` 中做了兼容：

1. 优先读取 `JWCH_USERNAME` / `JWCH_PASSWORD`。
2. 如果它们为空，自动回退到 `LEARN_USERNAME` / `LEARN_PASSWORD`。
3. 如果两组都没有，才会报缺少配置。

因此：

- 如果网络学堂和 Info / 教务使用同一套凭据，可以只填 `LEARN_USERNAME` / `LEARN_PASSWORD`。
- 如果未来 Info / 教务需要单独账号或单独密码，再填写 `JWCH_USERNAME` / `JWCH_PASSWORD`。
- `.env.example` 保留 `JWCH_USERNAME` / `JWCH_PASSWORD` 是为了支持这种分离场景，不是说当前 `.env` 必须填写它们。

## 为什么可以不设 `JWCH_ANCHOR_MONDAY`

`JWCH_ANCHOR_MONDAY` 只影响课表的“周几第几节”如何映射到真实日期。它不影响考试安排，也不影响能否抓取课表。

如果不设置：

- 课表解析器会使用固定周一 `1970-01-05`。
- 这样可以保证离线测试和 JSONL 输出稳定。
- 下游仍能读取课程名、节次、地点、教师等信息，但 `starts_at` / `ends_at` 是占位周日期。

如果你希望课表时间落到某个真实教学周，请设置一个周一日期：

```env
JWCH_ANCHOR_MONDAY=2026-05-18
```

或在命令行临时传入：

```powershell
python main.py --channel jwch --allow-network --anchor-monday 2026-05-18 --output storage/jwch.jsonl
```

## 网络学堂

网络学堂登录封装在 `LearnAdapter` 中，已支持：

- 自动发现清华统一认证登录页。
- 使用页面公开的 SM2 逻辑加密密码。
- 手机验证码 / 企业微信二次认证。
- 保存并复用 180 天信任设备材料。
- 登录成功后跟随网络学堂 roaming ticket。
- 对 `/b/` JSON 接口自动携带 XSRF 请求头。

首次需要二次认证时：

```powershell
python scripts\probe_learn_double_auth.py start --type mobile
python scripts\probe_learn_double_auth.py verify --code <手机验证码> --trust-device
```

信任设备材料默认保存在 `storage/learn_trust_device.json`，已被 `.gitignore` 忽略。

真实同步：

```powershell
python main.py --channel learn --allow-network --output storage/learn.jsonl
```

当前默认同步范围包括：

- 课程公告。
- 课程文件 / 课件列表。
- 作业列表、作业 DDL、详情页完整说明和教师下发附件。
- 问卷列表。
- 讨论列表。

网络学堂输出统一为 `CourseTask`。附件不会被嵌入 JSONL，而是以元数据形式保留下载地址、文件名、大小、扩展名等信息。需要保留 PDF、PPT、DOC、DOCX、ZIP 等文件本体时，请运行附件导出。

## 清华邮箱 IMAP

清华邮箱 IMAP 需要客户端专用密码，不要直接使用网页邮箱登录密码。

获取方式：

1. 登录清华邮箱网页端。
2. 进入 `设置` -> `安全设置` -> `客户端专用密码`。
3. 生成新的客户端专用密码。
4. 在 `.env` 中填写邮箱地址和该专用密码。

示例：

```env
MAIL_USERNAME=your_name@mails.tsinghua.edu.cn
MAIL_PASSWORD=your_client_specific_password
MAIL_BASE_URL=mails.tsinghua.edu.cn
MAIL_EXTRA_JSON={"imap_port":993,"use_ssl":true,"timeout_seconds":20}
```

清华邮箱常用参数：

- IMAP 收信服务器：`mails.tsinghua.edu.cn`
- IMAP SSL 端口：`993`
- IMAP 非 SSL 端口：`143`
- POP3 SSL 端口：`995`
- POP3 非 SSL 端口：`110`
- SMTP SSL 端口：`465`
- SMTP 非 SSL 端口：`25`

真实同步：

```powershell
python main.py --channel mail --allow-network --output storage/mail.jsonl --limit 50
```

邮箱同步使用 IMAP UID 做增量游标。游标保存在 SQLite 中，默认路径是 `storage/app.db`。邮件正文会做基础清洗，附件在 JSONL 中保存为 `imap://...` 元数据 URI；需要文件本体时使用附件导出脚本。

## Info / 教务

当前只关注两个页面：

- 考试安排：`JWCH_EXAM_URL`
- 课表：`JWCH_SCHEDULE_URL`

同步命令：

```powershell
python main.py --channel jwch --allow-network --output storage/jwch.jsonl
```

Info / 教务链路不是直接访问 `zhjw.cic.tsinghua.edu.cn` 内页，而是：

1. 登录 `info.tsinghua.edu.cn`。
2. 调用 Info 门户的 `onlineAppRedirect`。
3. 使用 `exam_app_id` / `schedule_app_id` 换取一次性 roaming URL。
4. 进入 JWCH 业务页面。
5. 将考试安排和课表解析为统一 `ScheduleItem`。

上述第 1 步登录 `info.tsinghua.edu.cn` 时，清华统一身份认证可能要求二次短信验证码（手机验证码 / 企业微信）。首次遇到二次认证时，使用专用探测脚本完成：

```powershell
python scripts\probe_jwch_double_auth.py start --type mobile
python scripts\probe_jwch_double_auth.py verify --code <手机验证码> --trust-device
```

`start` 步骤会提交账号密码并通过 `auth.cic.tsinghua.edu.cn` 向手机发送验证码，中间会话保存在 `storage/jwch_double_auth_session.json`。

`verify` 步骤提交验证码并可选信任设备（`--trust-device`）。信任设备材料保存在 `storage/jwch_trust_device.json`。

信任设备后，需要在 `.env` 的 `JWCH_EXTRA_JSON` 中将 `trust_path` 指向 JWCH 专用信任文件，以便后续自动登录复用：

```env
JWCH_EXTRA_JSON={"login_url":"https://info.tsinghua.edu.cn","username_field":"i_user","password_field":"i_pass","trust_path":"storage/jwch_trust_device.json","exam_app_id":"81008AA5A89C20D5BDBBDF719D5F0A94","schedule_app_id":"287C0C6D90ABB364CD5FDF1495199962","timeout_seconds":20}
```

如果网络学堂和 Info / 教务使用同一套账号且网络学堂已信任设备，也可以继续使用默认的 `storage/learn_trust_device.json`，不需要额外操作。`storage/jwch_double_auth_session.json`、`storage/jwch_trust_device.json` 已被 `.gitignore` 忽略。

如果账号密码方式没有拿到 Info 门户 API 认可的登录态，可以使用浏览器 cookie 兜底。新建 `storage/info_cookies.json`：

```json
{
  "JSESSIONID": "your_info_portal_jsessionid",
  "XSRF-TOKEN": "your_info_portal_xsrf_token"
}
```

然后在 `JWCH_EXTRA_JSON` 中追加：

```json
"info_cookie_path":"storage/info_cookies.json"
```

完整示例：

```env
JWCH_EXTRA_JSON={"login_url":"https://info.tsinghua.edu.cn","username_field":"i_user","password_field":"i_pass","trust_path":"storage/learn_trust_device.json","exam_app_id":"81008AA5A89C20D5BDBBDF719D5F0A94","schedule_app_id":"287C0C6D90ABB364CD5FDF1495199962","timeout_seconds":20,"info_cookie_path":"storage/info_cookies.json"}
```

不要把 cookie、ticket、验证码、session 文件发到聊天窗口或提交到 Git。

## 附件导出

JSONL 中只保存附件元数据；文件本体需要单独导出。

网络学堂附件：

```powershell
python scripts\export_attachments.py --source learn --jsonl storage\learn.jsonl --semester-start 2026-02-01 --limit 50
```

Learn 附件导出默认覆盖旧 manifest，只选择本学期课件，以及本学期中尚未提交或 DDL 尚未到期的作业附件。

邮箱附件：

```powershell
python scripts\export_attachments.py --source mail --limit 50
```

导出的文件保存在 `storage/attachments/`，导出清单保存在 `storage/attachments/manifest.jsonl`。这些路径都已被 `.gitignore` 忽略。

前端任务详情中的附件通过后端 `/api/tasks/{task_id}/attachments/{index}` 打开。后端只允许访问任务记录中已存在的网络学堂附件，并将首次下载结果缓存到 `storage/task_attachments/`，因此不会把用户直接跳转到网络学堂。

## 启动应用

前端生产资源已放在 `frontend_static/`。启动后端即可同时访问 API 和页面：

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000/app/
```

修改 React 前端后，重新构建：

```powershell
cd frontend
npm run build
```

## 课程资料解析

B 模块负责把本地课程资料解析为 C 模块可索引的标准文本块。默认支持 TXT、Markdown、PDF、DOCX、PPTX、图片、音频/视频。

**各格式文字提取与图片识别：**

| 格式 | 提取方式 | 图片处理 |
|------|---------|---------|
| TXT / Markdown | 直接读取文件 | — |
| PDF | `pypdf` 提取可选文本 + 页面嵌入图片 OCR | 嵌入图片单独 OCR；文字过少的页面整页渲染后 OCR |
| PPTX | `python-pptx` 提取形状/表格文字 + 幻灯片内图片 OCR | 幻灯片内嵌入图片单独 OCR |
| DOCX | `python-docx` 提取段落/表格文字 + 嵌入图片 OCR | 文档内嵌入图片单独 OCR（通过 ZIP 解包） |
| 图片文件 | Tesseract OCR | 整张图 OCR |
| 音频/视频 | `faster-whisper` ASR | — |

PDF、PPTX 和 DOCX 的嵌入图片 OCR 依赖本地安装的 **Tesseract OCR**（需含中文语言包 `chi_sim`）。若 Tesseract 未安装，图片 OCR 静默跳过，不影响文字提取；直接上传图片文件时则会提示 OCR 不可用。OCR 语言默认为 `chi_sim+eng`，可通过 `MATERIAL_OCR_LANG` 环境变量修改。

Windows 上可用 `winget` 安装 OCR 引擎：

```powershell
winget install --id UB-Mannheim.TesseractOCR --source winget --accept-package-agreements --accept-source-agreements
```

部分安装包只自带英文语言包。若需要识别中文题目截图，请下载中英文语言数据到本地用户目录：

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\LearningAssistantTools\tessdata" | Out-Null
Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata" -OutFile "$env:LOCALAPPDATA\LearningAssistantTools\tessdata\chi_sim.traineddata"
Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata" -OutFile "$env:LOCALAPPDATA\LearningAssistantTools\tessdata\eng.traineddata"
```

项目会自动尝试使用 `C:\Program Files\Tesseract-OCR\tesseract.exe` 和 `%LOCALAPPDATA%\LearningAssistantTools\tessdata`。如果安装在其他位置，可在 `.env` 中显式配置：

```env
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSDATA_PREFIX=C:\Users\<你的用户名>\AppData\Local\LearningAssistantTools\tessdata
MATERIAL_OCR_LANG=chi_sim+eng
```

音频/视频转写需要可选安装 `faster-whisper` 和 FFmpeg。

C 模块索引支持三种检索模式：**关键词**（jieba + TF-IDF）、**向量**（sentence-transformers + 余弦相似度）、**混合**（RRF 融合）。默认使用混合检索，自动结合两种方式的优势——关键词匹配精确术语，语义向量捕捉同义表达。

索引构建时会同时生成关键词和向量索引（需 `sentence-transformers`），若向量索引不可用则自动退化为纯关键词搜索。

解析 A 导出的附件：

```powershell
python scripts\parse_materials.py --manifest storage\attachments\manifest.jsonl --records-jsonl storage\learn.jsonl --output storage\material_chunks.jsonl
```

增量解析，跳过 `storage/material_chunks.jsonl` 中已有 `file_hash` 的文件，并自动去重：

```powershell
python scripts\parse_materials.py --manifest storage\attachments\manifest.jsonl --records-jsonl storage\learn.jsonl --output storage\material_chunks.jsonl --incremental
```

解析任意本地资料目录：

```powershell
python scripts\parse_materials.py --input path\to\course_materials --output storage\material_chunks.jsonl
```

先只检查解析数量、不写文件：

```powershell
python scripts\parse_materials.py --input tests\fixtures\material_sample.md --dry-run
```

输出为 `MaterialChunk` JSONL，核心字段包括：

- `chunk_id`
- `source_file`
- `file_hash`
- `material_type`
- `course_name`
- `title`
- `page` / `slide`
- `chunk_index`
- `start_char` / `end_char`
- `text_hash`
- `text`
- `metadata`

每次非 dry-run 解析还会生成 `storage/material_parse_report.json`，记录每个文件的解析状态、抽取字符数、chunk 数、抽取方式、失败原因和增量跳过情况。

## 输出格式

所有同步命令输出 JSONL，每行是一条结构化记录。

网络学堂和邮箱使用 `CourseTask`，核心字段包括：

- `source`：`learn` 或 `mail`
- `task_type`：`notice`、`homework`、`file`、`quiz`、`discussion`、`exam` 等
- `raw_id`：源系统原始 ID 或稳定指纹
- `course_name`
- `title`
- `content`
- `deadline`
- `attachments`

Info / 教务使用 `ScheduleItem`，核心字段包括：

- `source`：`jwch`
- `schedule_type`：`exam` 或 `class`
- `raw_id`
- `course_name`
- `title`
- `content`
- `starts_at`
- `ends_at`
- `location`
- `teacher`

## 去重与状态

默认 SQLite 路径：

```powershell
storage/app.db
```

它保存：

- 已输出记录指纹，避免重复写 JSONL。
- 邮箱 IMAP `last_uid` 游标。

测试时可以使用内存数据库，避免污染本地状态：

```powershell
python main.py --channel mail --allow-network --db-path ':memory:' --output storage/mail_test.jsonl
```

## 诊断命令

架构 harness：

```powershell
python scripts\run_harness.py
```

网络学堂登录探测：

```powershell
python scripts\probe_learn.py --allow-network --endpoint /
```

邮箱登录诊断：

```powershell
python scripts\probe_mail.py --diagnose-login
```

邮箱 UID 增量探测：

```powershell
python scripts\probe_mail.py --allow-network --limit 3 --db-path ':memory:'
```

Info / 教务页面探测：

```powershell
python scripts\probe_jwch.py --allow-network --target both
python scripts\probe_jwch.py --allow-network --target both --parse
```

指定课表真实周一：

```powershell
python scripts\probe_jwch.py --allow-network --target schedule --parse --anchor-monday 2026-05-18
```

## 本地文件与提交安全

这些文件或目录是本地运行产物，不应提交：

- `.env`
- `storage/*.jsonl`
- `storage/app.db`
- `storage/info_cookies.json`
- `storage/*session*.json`
- `storage/*trust*.json`
- `storage/attachments/`
- `logs/`

它们已经通过 `.gitignore` 忽略。建议通过 Git 提交代码，而不是把整个工作目录打包发给协作者，因为打包可能包含本地忽略文件。

## 当前限制

- 网络学堂中少数作业的详情页本身没有文字说明，此时 `content` 会为空，前端会提示查看附件或课程通知。
- 邮箱附件不会自动写入 JSONL，需要用 `scripts/export_attachments.py` 导出文件本体。
- 课表页面通常只给周几和节次；若不提供 `JWCH_ANCHOR_MONDAY`，日期字段会使用稳定占位周。
- 单渠道失败不会拖垮 `--channel all`，失败会记录日志并继续其他渠道。
