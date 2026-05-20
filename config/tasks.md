# Config Tasks

## 目标

提供可读、保守、可扩展的配置起点，使后续实现可以从统一配置中读取渠道开关、存储路径、日志策略、轮询间隔和解析默认行为。

## 边界

配置文件只允许表达非敏感默认值。真实凭证必须放在本地 `.env` 中，并通过环境变量读取。

允许：

- 渠道开关、默认 URL、超时、重试次数。
- SQLite、JSONL、日志文件路径。
- 轮询间隔、退避时间。
- Parser 行为默认值。
- `.env.example` 中的占位变量名。

禁止：

- 真实学号、密码、邮箱授权码、Cookie、Token 或 API Key。
- 个人本地绝对路径。
- 高频轮询默认值。
- 让 Parser 读取凭证或环境变量。

## 输入

- 架构文档中的四层流水线。
- Adapter、Parser、Pipeline 和 `main.py` 的最小配置需求。

## 输出

- `config/settings.yaml`
- `config/.env.example`
- `config/tasks.md`

## 任务列表

- [x] 增加 `channels`，覆盖 learn、mail、jwch 的开关和基础连接参数。
- [x] 增加 `storage`，定义 SQLite、JSONL、raw payload 默认路径和 fingerprint 算法。
- [x] 增加 `logging`，定义日志级别、文件、轮转和脱敏开关。
- [x] 增加 `polling`，定义保守轮询间隔和退避策略。
- [x] 增加 `parser`，定义清洗、正文长度和附件提取默认行为。
- [x] 增加 `.env.example` 占位变量，并与代码中的 `LEARN_`、`MAIL_`、`JWCH_` 前缀保持一致。
- [ ] 后续补充 `settings.local.yaml` 约定，并加入 `.gitignore`。

## 验收 harness

Plan 验收：

- 配置项只服务于 Adapters、Parsers、Pipeline 和 `main.py` 的已知需求。
- 敏感信息只能以变量名或占位值出现。

Implementation 验收：

- YAML 可读且结构稳定。
- 默认轮询频率保守：邮件 15 分钟、网络学堂 30 分钟、教务 120 分钟。
- `save_raw_payloads` 默认关闭。
- `.env.example` 不包含真实凭证或可用 Token。

Reflection 验收：

- 配置不鼓励跨层调用，例如 Parser 不读取账号密码。
- 后续实现可以按 channel 初始化对应 adapter/parser。
- 日志默认开启敏感值脱敏。

## 风险

- 不同环境可能需要不同配置文件，后续可增加 `settings.local.yaml`，但必须加入忽略规则。
- 下游 API 地址和鉴权方式尚未定型，目前只保留占位变量。
- 真实部署时需要补充配置加载优先级：默认 YAML、环境变量、本地覆盖。
