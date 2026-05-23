import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "./api";

type AnyRecord = Record<string, any>;

const sections = ["Dashboard", "任务中心", "资料页", "问答页", "总结页", "作业页", "设置"];

export function App() {
  const [active, setActive] = useState(sections[0]);
  const [dashboard, setDashboard] = useState<AnyRecord | null>(null);
  const [tasks, setTasks] = useState<AnyRecord[]>([]);
  const [materials, setMaterials] = useState<AnyRecord[]>([]);
  const [health, setHealth] = useState<AnyRecord | null>(null);
  const [qaResult, setQaResult] = useState<AnyRecord | null>(null);
  const [summaryResult, setSummaryResult] = useState<AnyRecord | null>(null);
  const [homeworkResult, setHomeworkResult] = useState<AnyRecord | null>(null);
  const [question, setQuestion] = useState("这门课最近有什么需要注意的内容？");
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

  async function askQuestion() {
    const result = await apiPost<AnyRecord>("/api/qa", { question });
    setQaResult(result);
  }

  async function summarize() {
    const result = await apiPost<AnyRecord>("/api/summaries", { topic: "本周课程资料" });
    setSummaryResult(result);
  }

  async function homeworkAssistant() {
    const result = await apiPost<AnyRecord>("/api/homework-assistant", {
      task_id: tasks[0]?.raw_id,
      question: "帮我分析这次作业的完成思路",
    });
    setHomeworkResult(result);
  }

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

        {error && <div className="error">后端连接失败：{error}</div>}
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

        {active === "任务中心" && (
          <Panel title="A 模块任务中心">
            <RecordList records={tasks} empty="等待 A 模块提供任务、公告、作业和附件数据" />
          </Panel>
        )}

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

        {active === "问答页" && (
          <Panel title="D 模块问答接口">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
            <button className="primary" onClick={askQuestion}>提问</button>
            {qaResult && <pre>{JSON.stringify(qaResult, null, 2)}</pre>}
          </Panel>
        )}

        {active === "总结页" && (
          <Panel title="D 模块资料总结">
            <button className="primary" onClick={summarize}>生成 Mock 总结</button>
            {summaryResult && <pre>{JSON.stringify(summaryResult, null, 2)}</pre>}
          </Panel>
        )}

        {active === "作业页" && (
          <Panel title="D 模块作业助手">
            <p>作业要求来自 A，相关知识由 C 检索，最终建议由 D 生成。</p>
            <button className="primary" onClick={homeworkAssistant}>生成作业思路</button>
            {homeworkResult && <pre>{JSON.stringify(homeworkResult, null, 2)}</pre>}
          </Panel>
        )}

        {active === "设置" && (
          <Panel title="模块状态与接口说明">
            <p>前端只调用 E 模块后端统一 API。A/B 已有数据直接接入，C/D 暂以 Mock 保持演示链路。</p>
            <pre>{JSON.stringify(health, null, 2)}</pre>
          </Panel>
        )}
      </main>
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
  if (!records.length) {
    return <p className="muted">{empty}</p>;
  }
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
