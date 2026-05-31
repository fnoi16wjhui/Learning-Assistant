import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "./api";

type AnyRecord = Record<string, any>;

const sections = ["Dashboard", "任务中心", "资料页", "问答页", "总结页", "作业页", "设置"];

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function extractCourses(tasks: AnyRecord[]): string[] {
  const seen = new Set<string>();
  const courses: string[] = [];
  for (const t of tasks) {
    const name = (t.course_name || "").trim();
    if (name && name !== "Unknown Course" && !seen.has(name)) {
      seen.add(name);
      courses.push(name);
    }
  }
  courses.sort((a, b) => a.localeCompare(b, "zh"));
  return courses;
}

/* ------------------------------------------------------------------ */
/* App                                                                 */
/* ------------------------------------------------------------------ */

export function App() {
  const [active, setActive] = useState(sections[0]);
  const [dashboard, setDashboard] = useState<AnyRecord | null>(null);
  const [tasks, setTasks] = useState<AnyRecord[]>([]);
  const [materials, setMaterials] = useState<AnyRecord[]>([]);
  const [health, setHealth] = useState<AnyRecord | null>(null);

  // QA
  const [qaResult, setQaResult] = useState<AnyRecord | null>(null);
  const [qaLoading, setQaLoading] = useState(false);
  const [question, setQuestion] = useState("最近有什么作业要交？");
  const [qaCourse, setQaCourse] = useState("");

  // Summary
  const [summaryResult, setSummaryResult] = useState<AnyRecord | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryCourse, setSummaryCourse] = useState("");
  const [summaryTopic, setSummaryTopic] = useState("");

  // Homework
  const [homeworkResult, setHomeworkResult] = useState<AnyRecord | null>(null);
  const [homeworkLoading, setHomeworkLoading] = useState(false);
  const [hwTaskId, setHwTaskId] = useState("");
  const [hwQuestion, setHwQuestion] = useState("帮我分析这次作业的完成思路");

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      apiGet<AnyRecord>("/api/dashboard"),
      apiGet<AnyRecord>("/api/tasks"),
      apiGet<AnyRecord>("/api/materials"),
      apiGet<AnyRecord>("/api/health"),
    ])
      .then(([dashboardData, taskData, materialData, healthData]) => {
        setDashboard(dashboardData);
        setTasks(taskData.items ?? []);
        setMaterials(materialData.items ?? []);
        setHealth(healthData);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const stats = dashboard?.stats ?? {};
  const moduleBadges = useMemo(() => Object.entries(health?.modules ?? {}), [health]);
  const courses = useMemo(() => extractCourses(tasks), [tasks]);

  // ---- API calls ----------------------------------------------------------

  async function askQuestion() {
    setQaLoading(true);
    setError(null);
    setQaResult(null);
    try {
      const body: AnyRecord = { question };
      if (qaCourse) body.course_name = qaCourse;
      const result = await apiPost<AnyRecord>("/api/qa", body);
      setQaResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "问答请求失败");
    } finally {
      setQaLoading(false);
    }
  }

  async function summarize() {
    setSummaryLoading(true);
    setError(null);
    setSummaryResult(null);
    try {
      const body: AnyRecord = {};
      if (summaryCourse) body.course_name = summaryCourse;
      if (summaryTopic) body.topic = summaryTopic;
      else body.topic = "课程内容总结";
      const result = await apiPost<AnyRecord>("/api/summaries", body);
      setSummaryResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "总结请求失败");
    } finally {
      setSummaryLoading(false);
    }
  }

  async function homeworkAssistant() {
    setHomeworkLoading(true);
    setError(null);
    setHomeworkResult(null);
    try {
      const body: AnyRecord = { question: hwQuestion };
      if (hwTaskId) body.task_id = hwTaskId;
      const result = await apiPost<AnyRecord>("/api/homework-assistant", body);
      setHomeworkResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "作业助手请求失败");
    } finally {
      setHomeworkLoading(false);
    }
  }

  function handleQuestionKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && e.ctrlKey) askQuestion();
  }

  // ---- Course-aware task filter for homework page --------------------------

  const homeworkTasks = useMemo(() => {
    if (!summaryCourse) return tasks.filter((t) => t.task_type === "homework");
    return tasks.filter(
      (t) => t.task_type === "homework" && t.course_name === summaryCourse
    );
  }, [tasks, summaryCourse]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h1>Learning Assistant</h1>
        <p>E 模块产品平台</p>
        {sections.map((section) => (
          <button key={section} className={active === section ? "active" : ""} onClick={() => setActive(section)}>
            {section}
          </button>
        ))}
      </aside>
      <main className="content">
        <header>
          <div>
            <span className="eyebrow">A/B/C/D/E Integration</span>
            <h2>{active}</h2>
          </div>
          <div className="badges">
            {moduleBadges.map(([name, status]) => (
              <span key={name} className={`badge ${status}`}>
                {name}: {String(status)}
              </span>
            ))}
          </div>
        </header>

        {error && <div className="error" onClick={() => setError(null)}>{error}（点击关闭）</div>}

        {/* ============ Dashboard =========================================== */}

        {active === "Dashboard" && (
          <section className="grid">
            <StatCard title="任务数" value={stats.task_count ?? 0} module="A" />
            <StatCard title="课表/考试" value={stats.schedule_count ?? 0} module="A" />
            <StatCard title="资料片段" value={stats.material_count ?? 0} module="B" />
            <StatCard title="待处理作业" value={stats.pending_homework_count ?? 0} module="A/D" />
            <Panel title="最近任务">
              <RecordList records={dashboard?.recent_tasks ?? []} empty="暂无 A 模块任务数据" />
            </Panel>
            <Panel title="同步与知识库状态">
              <RecordList records={[...(dashboard?.sync_status ?? []), dashboard?.knowledge_status].filter(Boolean)} />
            </Panel>
          </section>
        )}

        {/* ============ 任务中心 =========================================== */}

        {active === "任务中心" && (
          <Panel title="A 模块任务中心">
            <RecordList records={tasks} empty="等待 A 模块提供任务、公告、作业和附件数据" />
          </Panel>
        )}

        {/* ============ 资料页 ============================================= */}

        {active === "资料页" && (
          <section className="grid">
            <Panel title="B 模块解析结果">
              <RecordList records={materials} empty="等待 B 模块输出 material_chunks.jsonl" />
            </Panel>
            <Panel title="C 模块知识库状态">
              <pre>{JSON.stringify(dashboard?.knowledge_status ?? {}, null, 2)}</pre>
            </Panel>
          </section>
        )}

        {/* ============ 问答页 ============================================= */}

        {active === "问答页" && (
          <Panel title="D 模块 — 课程问答">
            <p className="muted" style={{ marginTop: 0 }}>
              自动规划检索：A 模块（任务/邮件/考试）+ C 模块（课件资料）
            </p>

            <div className="form-row">
              <label className="field">
                <span>选择课程（可选，不选则自动判断）</span>
                <select value={qaCourse} onChange={(e) => setQaCourse(e.target.value)}>
                  <option value="">全部课程</option>
                  {courses.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
            </div>

            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleQuestionKeyDown}
              placeholder="输入你的问题...（Ctrl+Enter 发送）"
            />
            <button className="primary" onClick={askQuestion} disabled={qaLoading}>
              {qaLoading ? "思考中..." : "提问"}
            </button>

            {qaLoading && <ThinkingBanner />}

            {qaResult && !qaLoading && (
              <div className="answer-card">
                <ResultMeta result={qaResult} />
                <div className="answer-body">{qaResult.answer}</div>
                <CitationList citations={qaResult.citations} />
              </div>
            )}
          </Panel>
        )}

        {/* ============ 总结页 ============================================= */}

        {active === "总结页" && (
          <Panel title="D 模块 — 资料总结">
            <div className="form-row">
              <label className="field">
                <span>课程</span>
                <select value={summaryCourse} onChange={(e) => setSummaryCourse(e.target.value)}>
                  <option value="">请选择课程</option>
                  {courses.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>总结主题（可选）</span>
                <input
                  type="text"
                  value={summaryTopic}
                  onChange={(e) => setSummaryTopic(e.target.value)}
                  placeholder="如：第14周内容、期中复习"
                />
              </label>
            </div>
            <button className="primary" onClick={summarize} disabled={summaryLoading}>
              {summaryLoading ? "生成中..." : "生成课程总结"}
            </button>
            {summaryLoading && <ThinkingBanner />}
            {summaryResult && !summaryLoading && (
              <div className="answer-card">
                <ResultMeta result={summaryResult} />
                <div className="answer-body">{summaryResult.summary}</div>
                {summaryResult.key_points?.length > 0 && (
                  <div className="key-points">
                    <h4>核心要点</h4>
                    <ul>
                      {summaryResult.key_points.map((kp: string, i: number) => (
                        <li key={i}>{kp}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <CitationList citations={summaryResult.citations} />
              </div>
            )}
          </Panel>
        )}

        {/* ============ 作业页 ============================================= */}

        {active === "作业页" && (
          <Panel title="D 模块 — 作业助手">
            <p className="muted" style={{ marginTop: 0 }}>
              作业要求来自 A 模块，知识检索来自 C 模块，解题建议由 D 生成
            </p>
            <div className="form-row">
              <label className="field">
                <span>选择作业（可选）</span>
                <select value={hwTaskId} onChange={(e) => setHwTaskId(e.target.value)}>
                  <option value="">不指定具体作业</option>
                  {homeworkTasks.map((t) => (
                    <option key={t.raw_id} value={t.raw_id}>
                      [{t.course_name}] {t.title}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <textarea
              value={hwQuestion}
              onChange={(e) => setHwQuestion(e.target.value)}
              placeholder="描述你的问题..."
            />
            <button className="primary" onClick={homeworkAssistant} disabled={homeworkLoading}>
              {homeworkLoading ? "生成中..." : "生成作业思路"}
            </button>
            {homeworkLoading && <ThinkingBanner />}
            {homeworkResult && !homeworkLoading && (
              <div className="answer-card">
                <ResultMeta result={homeworkResult} />
                {homeworkResult.outline?.length > 0 && (
                  <div className="section-block">
                    <h4>解题步骤</h4>
                    <ol>
                      {homeworkResult.outline.map((s: string, i: number) => (
                        <li key={i}>{s}</li>
                      ))}
                    </ol>
                  </div>
                )}
                {homeworkResult.draft && (
                  <div className="section-block">
                    <h4>思路草稿</h4>
                    <div className="answer-body">{homeworkResult.draft}</div>
                  </div>
                )}
                {homeworkResult.pitfalls?.length > 0 && (
                  <div className="section-block">
                    <h4>常见注意点</h4>
                    <ul>
                      {homeworkResult.pitfalls.map((p: string, i: number) => (
                        <li key={i}>{p}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {homeworkResult.checklist?.length > 0 && (
                  <div className="section-block">
                    <h4>自查清单</h4>
                    <ul>
                      {homeworkResult.checklist.map((c: string, i: number) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <CitationList citations={homeworkResult.citations} />
              </div>
            )}
          </Panel>
        )}

        {/* ============ 设置 =============================================== */}

        {active === "设置" && (
          <Panel title="模块状态与接口说明">
            <p>前端只调用 E 模块后端统一 API。A/B 已有数据直接接入，C/D 通过 LLM 提供智能服务。</p>
            <pre>{JSON.stringify(health, null, 2)}</pre>
          </Panel>
        )}
      </main>
    </div>
  );
}

/* ================================================================== */
/* Sub-components                                                      */
/* ================================================================== */

function ThinkingBanner() {
  return (
    <div className="thinking-banner">
      <span className="spinner" />
      <div>
        <strong>AI 正在思考</strong>
        <p>正在分析问题、检索相关资料并生成回答，请稍候...</p>
      </div>
    </div>
  );
}

function ResultMeta({ result }: { result: AnyRecord }) {
  const r = result.retrieved;
  return (
    <div className="result-meta">
      <span className={`status-tag ${result.status}`}>
        {result.status === "ready" ? "已生成" : "调用失败"}
      </span>
      {r && (
        <span className="retrieved-info">
          A模块 {r.a_module ?? 0} 条 · C模块 {r.c_module ?? 0} 条
          {r.queries_used?.length > 0 && <>{" "}· 搜索词: {r.queries_used.join(", ")}</>}
        </span>
      )}
    </div>
  );
}

function CitationList({ citations }: { citations: AnyRecord[] | undefined }) {
  if (!citations?.length) return null;
  return (
    <div className="citations">
      <h4>引用来源</h4>
      {citations.map((c, i) => (
        <div key={i} className="citation-item">
          <div className="citation-title">{c.title}</div>
          <div className="citation-snippet">{c.snippet}</div>
          <div className="citation-source">{c.source}</div>
        </div>
      ))}
    </div>
  );
}

function StatCard({ title, value, module }: { title: string; value: number; module: string }) {
  return (
    <div className="stat-card">
      <span>{module}</span>
      <strong>{value}</strong>
      <p>{title}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function RecordList({ records, empty = "暂无数据" }: { records: AnyRecord[]; empty?: string }) {
  if (!records.length) return <p className="muted">{empty}</p>;
  return (
    <div className="record-list">
      {records.slice(0, 8).map((record, index) => (
        <article key={`${record.raw_id ?? record.title ?? index}`}>
          <div>
            <strong>{record.title ?? record.course_name ?? record.channel ?? record.source_file ?? "未命名记录"}</strong>
            <span>{record.source_module ?? record.status ?? "E"}</span>
          </div>
          <p>{record.content ?? record.text ?? record.message ?? record.status ?? "已接入统一接口"}</p>
        </article>
      ))}
    </div>
  );
}
