import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiUrl } from "./api";

type AnyRecord = Record<string, any>;
type TaskView = "pending" | "homework" | "all";

type Preferences = {
  semesterId: string;
  recentLimit: number;
  taskView: TaskView;
};

const pages = [
  { id: "home", label: "学习概览", title: "学习概览" },
  { id: "tasks", label: "任务与截止", title: "任务与截止" },
  { id: "qa", label: "课程问答", title: "课程问答" },
  { id: "summary", label: "学习总结", title: "学习总结" },
  { id: "homework", label: "作业助手", title: "作业助手" },
  { id: "settings", label: "偏好设置", title: "偏好设置" },
] as const;

const semesters = [
  { id: "2025-2026-2", label: "2025-2026 春季学期", start: "2026-02-01", end: "2026-08-31" },
  { id: "2025-2026-1", label: "2025-2026 秋季学期", start: "2025-09-01", end: "2026-01-31" },
  { id: "2024-2025-2", label: "2024-2025 春季学期", start: "2025-02-01", end: "2025-08-31" },
] as const;

const defaultPreferences: Preferences = {
  semesterId: "2025-2026-2",
  recentLimit: 4,
  taskView: "pending",
};

export function App() {
  const [active, setActive] = useState<(typeof pages)[number]["id"]>("home");
  const [dashboard, setDashboard] = useState<AnyRecord | null>(null);
  const [tasks, setTasks] = useState<AnyRecord[]>([]);
  const [materials, setMaterials] = useState<AnyRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [preferences, setPreferences] = useState<Preferences>(loadPreferences);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [qaResult, setQaResult] = useState<AnyRecord | null>(null);
  const [qaLoading, setQaLoading] = useState(false);
  const [question, setQuestion] = useState("最近有什么作业要交？");
  const [qaCourse, setQaCourse] = useState("");

  const [summaryResult, setSummaryResult] = useState<AnyRecord | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryCourse, setSummaryCourse] = useState("");
  const [summaryMaterialId, setSummaryMaterialId] = useState("");
  const [summaryTopic, setSummaryTopic] = useState("");
  const [summaryPage, setSummaryPage] = useState<number | null>(null);

  const [homeworkResult, setHomeworkResult] = useState<AnyRecord | null>(null);
  const [homeworkLoading, setHomeworkLoading] = useState(false);
  const [hwCourse, setHwCourse] = useState("");
  const [hwTaskId, setHwTaskId] = useState("");
  const [hwQuestion, setHwQuestion] = useState("帮我分析这次作业的完成思路");
  const [uploadedFiles, setUploadedFiles] = useState<{ name: string; text: string; loading: boolean }[]>([]);

  const [slowOperation, setSlowOperation] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildResult, setRebuildResult] = useState<string | null>(null);

  const [llmConfig, setLlmConfig] = useState<AnyRecord | null>(null);
  const [llmConfigLoading, setLlmConfigLoading] = useState(false);
  const [llmConfigSaved, setLlmConfigSaved] = useState(false);

  async function refreshData() {
    const [dashboardData, taskData, materialData] = await Promise.all([
      apiGet<AnyRecord>("/api/dashboard"),
      apiGet<AnyRecord>("/api/tasks?limit=200"),
      apiGet<AnyRecord>("/api/materials?limit=200"),
    ]);
    setDashboard(dashboardData);
    setTasks(taskData.items ?? []);
    setMaterials(materialData.items ?? []);
  }

  useEffect(() => {
    refreshData().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    localStorage.setItem("learning-assistant-preferences", JSON.stringify(preferences));
  }, [preferences]);

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), 8000);
    return () => window.clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    const loading = qaLoading || summaryLoading || homeworkLoading;
    if (!loading) {
      setSlowOperation(null);
      return;
    }
    const timer = window.setTimeout(() => setSlowOperation("LLM 调用耗时较长，请耐心等待..."), 15000);
    return () => window.clearTimeout(timer);
  }, [qaLoading, summaryLoading, homeworkLoading]);

  useEffect(() => {
    if (active === "settings") loadLlmConfig();
  }, [active]);

  const semester = useMemo(
    () => semesters.find((item) => item.id === preferences.semesterId) ?? semesters[0],
    [preferences.semesterId],
  );
  const semesterTasks = useMemo(
    () => tasks.filter((task) => recordBelongsToSemester(task, semester)),
    [tasks, semester],
  );
  const homeworkTasks = useMemo(
    () => semesterTasks.filter(
      (task) =>
        task.task_type === "homework"
        && (task.source === "learn" || Boolean(task.ddl) || Boolean(task.status)),
    ),
    [semesterTasks],
  );
  const semesterMaterials = useMemo(
    () => materials.filter((material) => recordBelongsToSemester(material, semester)),
    [materials, semester],
  );
  const pendingHomework = useMemo(
    () => homeworkTasks.filter((task) => task.completed === false || task.status === "unsubmitted"),
    [homeworkTasks],
  );
  const taskPool = useMemo(() => {
    if (preferences.taskView === "pending") return pendingHomework;
    if (preferences.taskView === "homework") return homeworkTasks;
    return semesterTasks;
  }, [homeworkTasks, pendingHomework, preferences.taskView, semesterTasks]);
  const selectedTask = useMemo(
    () => taskPool.find((task) => taskRecordId(task) === selectedTaskId) ?? taskPool[0] ?? null,
    [selectedTaskId, taskPool],
  );
  const courses = useMemo(
    () => extractCourses([...semesterTasks, ...semesterMaterials]),
    [semesterMaterials, semesterTasks],
  );
  const homeworkCourses = useMemo(() => extractCourses(homeworkTasks), [homeworkTasks]);
  const summaryFiles = useMemo(
    () => semesterMaterials.filter(
      (material) => !summaryCourse || material.course_name === summaryCourse,
    ),
    [semesterMaterials, summaryCourse],
  );
  const homeworkTasksForCourse = useMemo(
    () => homeworkTasks.filter((task) => task.course_name === hwCourse),
    [homeworkTasks, hwCourse],
  );
  const selectedHwTask = useMemo(
    () => homeworkTasksForCourse.find((task) => task.raw_id === hwTaskId) ?? null,
    [homeworkTasksForCourse, hwTaskId],
  );
  const selectedMaterial = useMemo(
    () => summaryFiles.find((item) => materialRecordId(item) === summaryMaterialId) ?? null,
    [summaryFiles, summaryMaterialId],
  );

  useEffect(() => {
    if (!taskPool.length) {
      setSelectedTaskId("");
      return;
    }
    if (!selectedTaskId || !taskPool.some((task) => taskRecordId(task) === selectedTaskId)) {
      setSelectedTaskId(taskRecordId(taskPool[0]));
    }
  }, [selectedTaskId, taskPool]);

  const upcomingCount = homeworkTasks.filter((task) => isFutureDeadline(task.ddl)).length;
  const courseCount = new Set(
    semesterTasks.map((task) => task.course_name).filter((name) => name && name !== "Unknown Course"),
  ).size;
  const recentTasks = pendingHomework.slice(0, preferences.recentLimit);
  const currentPage = pages.find((page) => page.id === active) ?? pages[0];

  async function syncLatest() {
    setSyncing(true);
    setSyncMessage("正在同步网络学堂...");
    setError(null);
    try {
      const before = await learnSyncTimestamp();
      await apiPost("/api/sync/run", {
        channel: "learn",
        allow_network: true,
        semester_id: preferences.semesterId,
      });
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await delay(2000);
        const after = await learnSyncTimestamp();
        if (after && after !== before) {
          await refreshData();
          setSyncMessage("已同步到最新");
          return;
        }
      }
      setSyncMessage("同步仍在后台进行，可稍后刷新查看");
    } catch (err) {
      setError(err instanceof Error ? err.message : "同步失败");
      setSyncMessage("");
    } finally {
      setSyncing(false);
    }
  }

  async function askQuestion() {
    setQaLoading(true);
    setError(null);
    setQaResult(null);
    try {
      const body: AnyRecord = { question };
      if (qaCourse) body.course_name = qaCourse;
      setQaResult(await apiPost<AnyRecord>("/api/qa", body));
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
      const body: AnyRecord = { topic: summaryTopic || "课程内容总结" };
      if (summaryCourse) body.course_name = summaryCourse;
      if (summaryMaterialId) body.material_id = summaryMaterialId;
      if (summaryPage !== null) body.page = summaryPage;
      setSummaryResult(await apiPost<AnyRecord>("/api/summaries", body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "总结请求失败");
    } finally {
      setSummaryLoading(false);
    }
  }

  async function uploadFile(file: File) {
    const entry = { name: file.name, text: "", loading: true };
    setUploadedFiles((prev) => [...prev, entry]);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(apiUrl("/api/upload"), { method: "POST", body: formData });
      if (!response.ok) throw new Error(`上传失败: ${response.status}`);
      const data = await response.json() as AnyRecord;
      setUploadedFiles((prev) =>
        prev.map((f) => (f.name === file.name ? { name: file.name, text: data.text || "", loading: false } : f))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "文件上传失败");
      setUploadedFiles((prev) => prev.filter((f) => f.name !== file.name));
    }
  }

  function removeUpload(name: string) {
    setUploadedFiles((prev) => prev.filter((f) => f.name !== name));
  }

  async function homeworkAssistant() {
    if (!hwCourse || !hwTaskId) {
      setError("请先选择课程，再选择具体作业");
      return;
    }
    setHomeworkLoading(true);
    setError(null);
    setHomeworkResult(null);
    try {
      const body: AnyRecord = { question: hwQuestion };
      if (hwTaskId) body.task_id = hwTaskId;
      const texts = uploadedFiles.filter((f) => f.text).map((f) => f.text);
      if (texts.length > 0) body.upload_texts = texts;
      setHomeworkResult(await apiPost<AnyRecord>("/api/homework-assistant", body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "作业助手请求失败");
    } finally {
      setHomeworkLoading(false);
    }
  }

  async function rebuildKnowledge() {
    setRebuilding(true);
    setRebuildResult(null);
    setError(null);
    try {
      const result = await apiPost<AnyRecord>("/api/knowledge/rebuild", {});
      setRebuildResult(`重建完成：已索引 ${result.indexed_chunks} 个文本块，模式 ${result.index_types?.join("、") ?? "未知"}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "知识库重建失败");
    } finally {
      setRebuilding(false);
    }
  }

  async function loadLlmConfig() {
    setLlmConfigLoading(true);
    try {
      const data = await apiGet<AnyRecord>("/api/settings/llm");
      setLlmConfig(data.config ?? null);
    } catch {
      // ignore
    } finally {
      setLlmConfigLoading(false);
    }
  }

  async function saveLlmConfig() {
    if (!llmConfig) return;
    setLlmConfigSaved(false);
    setError(null);
    try {
      const data = await apiPost<AnyRecord>("/api/settings/llm", {
        base_url: llmConfig.base_url,
        api_key: llmConfig.api_key,
        model: llmConfig.model,
        timeout: llmConfig.timeout,
        max_tokens: llmConfig.max_tokens,
      });
      setLlmConfig(data.config ?? null);
      setLlmConfigSaved(true);
      setTimeout(() => setLlmConfigSaved(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "LLM 配置保存失败");
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark">LA</div>
        <h1>Learning Assistant</h1>
        <p>课程、截止与学习助手</p>
        <nav>
          {pages.map((page) => (
            <button
              key={page.id}
              className={active === page.id ? "active" : ""}
              onClick={() => setActive(page.id)}
            >
              {page.label}
            </button>
          ))}
        </nav>
      </aside>

      <main className="content">
        <header>
          <div>
            <span className="eyebrow">{semester.label}</span>
            <h2>{currentPage.title}</h2>
          </div>
          {active === "home" && (
            <div className="sync-actions">
              {syncMessage && <span>{syncMessage}</span>}
              <button className="sync-button" onClick={syncLatest} disabled={syncing}>
                {syncing ? "同步中..." : "同步到最新"}
              </button>
            </div>
          )}
        </header>

        {error && (
          <button className="error" onClick={() => setError(null)}>
            {error}（点击关闭）
          </button>
        )}

        {active === "home" && (
          <section className="dashboard">
            <div className="stats-row">
              <StatCard title="待完成作业" value={pendingHomework.length} hint="需要继续处理" />
              <StatCard title="临近截止" value={upcomingCount} hint="本学期有明确截止时间" />
              <StatCard title="当前课程" value={courseCount || dashboard?.stats?.course_count || 0} hint="已识别课程" />
              <StatCard title="本学期作业" value={homeworkTasks.length} hint="包含已提交和待完成" />
            </div>
            <Panel title="近期任务" className="recent-panel">
              <RecordList
                records={recentTasks}
                empty="当前没有待完成作业"
                limit={preferences.recentLimit}
                showDeadline
                onSelect={(task) => {
                  setSelectedTaskId(taskRecordId(task));
                  setActive("tasks");
                }}
              />
            </Panel>
          </section>
        )}

        {active === "tasks" && (
          <TaskCenter
            tasks={taskPool}
            selectedTask={selectedTask}
            selectedTaskId={selectedTaskId}
            taskView={preferences.taskView}
            onTaskViewChange={(taskView) => setPreferences((current) => ({ ...current, taskView }))}
            onSelect={(task) => setSelectedTaskId(taskRecordId(task))}
          />
        )}

        {active === "qa" && (
          <Panel title="向课程助手提问" className="wide-panel">
            <p className="muted intro">自动检索当前学期的任务、通知和课程资料后生成回答。</p>
            <div className="form-row">
              <label className="field">
                <span>课程范围</span>
                <select value={qaCourse} onChange={(event) => setQaCourse(event.target.value)}>
                  <option value="">自动判断课程</option>
                  {courses.map((course) => <option key={course} value={course}>{course}</option>)}
                </select>
              </label>
            </div>
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && event.ctrlKey) askQuestion();
              }}
              placeholder="输入课程相关问题，Ctrl+Enter 发送"
            />
            <button className="primary" onClick={askQuestion} disabled={qaLoading}>
              {qaLoading ? "正在分析..." : "开始提问"}
            </button>
            {qaLoading && <ThinkingBanner />}
            {slowOperation && qaLoading && <div className="slow-banner">{slowOperation}</div>}
            {qaResult && !qaLoading && (
              <div className="answer-card">
                <ResultMeta result={qaResult} />
                <div className="answer-body">{qaResult.answer}</div>
                <CitationList citations={qaResult.citations} />
              </div>
            )}
          </Panel>
        )}

        {active === "summary" && (
          <section className="workspace-layout">
            <div className="workspace-main">
              <Panel title="生成课程学习总结" className="wide-panel">
                <div className="form-row">
                  <label className="field">
                    <span>课程</span>
                    <select
                      value={summaryCourse}
                      onChange={(event) => {
                        setSummaryCourse(event.target.value);
                        setSummaryMaterialId("");
                        setSummaryPage(null);
                      }}
                    >
                      <option value="">全部课程</option>
                      {courses.map((course) => <option key={course} value={course}>{course}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>指定文件（可选）</span>
                    <select
                      value={summaryMaterialId}
                      onChange={(event) => {
                        const materialId = event.target.value;
                        setSummaryMaterialId(materialId);
                        const material = summaryFiles.find((item) => materialRecordId(item) === materialId);
                        if (material?.course_name) setSummaryCourse(material.course_name);
                      }}
                    >
                      <option value="">综合当前课程的全部资料</option>
                      {summaryFiles.map((material) => (
                        <option key={materialRecordId(material)} value={materialRecordId(material)}>
                          {material.title ?? material.file_name ?? "未命名文件"}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="field summary-question">
                  <span>总结要求或针对文件的问题</span>
                  <textarea
                    value={summaryTopic}
                    onChange={(event) => setSummaryTopic(event.target.value)}
                    placeholder={'例如：总结这份文件的核心内容；或提问"拉普拉斯变换适用于哪些边界条件？"'}
                  />
                </label>
                <button className="primary" onClick={summarize} disabled={summaryLoading}>
                  {summaryLoading ? "正在生成..." : summaryMaterialId ? "分析指定文件" : "生成学习总结"}
                </button>
                {summaryLoading && <ThinkingBanner />}
                {slowOperation && summaryLoading && <div className="slow-banner">{slowOperation}</div>}
                {summaryResult && !summaryLoading && (
                  <div className="answer-card">
                    <ResultMeta result={summaryResult} />
                    <div className="answer-body">{summaryResult.summary}</div>
                    {summaryResult.key_points?.length > 0 && (
                      <div className="key-points">
                        <h4>核心要点</h4>
                        <ul>{summaryResult.key_points.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>
                      </div>
                    )}
                    <CitationList citations={summaryResult.citations} />
                  </div>
                )}
              </Panel>
            </div>
            <aside className="workspace-sidebar">
              <MaterialSidebar
                material={selectedMaterial}
                materialId={summaryMaterialId}
                focusedPage={summaryPage}
                course={summaryCourse}
                onAsk={(question, page) => {
                  setSummaryTopic(question);
                  if (page !== undefined) setSummaryPage(page);
                }}
                onPageFocus={setSummaryPage}
              />
            </aside>
          </section>
        )}

        {active === "homework" && (
          <section className="workspace-layout">
            <div className="workspace-main">
              <Panel title="拆解作业并生成完成思路" className="wide-panel">
                <p className="muted intro">结合完整作业说明和课程资料，给出步骤、注意点和自查清单。</p>
                <div className="form-row">
                  <label className="field">
                    <span>1. 选择课程</span>
                    <select
                      value={hwCourse}
                      onChange={(event) => {
                        setHwCourse(event.target.value);
                        setHwTaskId("");
                        setUploadedFiles([]);
                      }}
                    >
                      <option value="">请选择课程</option>
                      {homeworkCourses.map((course) => <option key={course} value={course}>{course}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>2. 选择具体作业</span>
                    <select
                      value={hwTaskId}
                      onChange={(event) => setHwTaskId(event.target.value)}
                      disabled={!hwCourse}
                    >
                      <option value="">{hwCourse ? "请选择作业" : "请先选择课程"}</option>
                      {homeworkTasksForCourse.map((task) => (
                        <option key={taskRecordId(task)} value={task.raw_id}>
                          {task.title}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="upload-area">
                  <span className="upload-label">
                    上传题目文件（可选 — 拍照或拖入题目图片/PDF，作业描述不清晰时使用）
                  </span>
                  <label className="upload-dropzone"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      Array.from(e.dataTransfer.files).forEach((f) => uploadFile(f));
                    }}
                  >
                    <input
                      type="file"
                      accept=".pdf,.pptx,.docx,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,.txt,.md"
                      multiple
                      style={{ display: "none" }}
                      onChange={(e) => {
                        if (e.target.files) Array.from(e.target.files).forEach((f) => uploadFile(f));
                        e.target.value = "";
                      }}
                    />
                    <span className="drop-hint">点击上传或拖放文件到此处</span>
                  </label>
                  {uploadedFiles.length > 0 && (
                    <div className="upload-file-list">
                      {uploadedFiles.map((f) => (
                        <div key={f.name} className="upload-file-item">
                          <span className="upload-file-name">
                            {f.loading ? "⏳" : "📎"} {f.name}
                          </span>
                          {f.loading && <span className="upload-file-status">解析中...</span>}
                          {!f.loading && f.text && (
                            <span className="upload-file-status">
                              {f.text.length} 字符
                            </span>
                          )}
                          <button className="upload-file-remove" onClick={() => removeUpload(f.name)}>
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <textarea value={hwQuestion} onChange={(event) => setHwQuestion(event.target.value)} />
                <button
                  className="primary"
                  onClick={homeworkAssistant}
                  disabled={homeworkLoading || !hwCourse || !hwTaskId}
                >
                  {homeworkLoading ? "正在生成..." : "生成完成思路"}
                </button>
                {homeworkLoading && <ThinkingBanner />}
                {slowOperation && homeworkLoading && <div className="slow-banner">{slowOperation}</div>}
                {homeworkResult && !homeworkLoading && <HomeworkResult result={homeworkResult} />}
              </Panel>
            </div>
            <aside className="workspace-sidebar">
              <HwTaskSidebar task={selectedHwTask} />
            </aside>
          </section>
        )}

        {active === "settings" && (
          <Panel title="学习范围与显示偏好" className="settings-panel">
            <div className="settings-grid">
              <label className="setting-item">
                <strong>当前学期</strong>
                <span>切换后，首页、任务和课程选择都会使用对应学期的数据。</span>
                <select
                  value={preferences.semesterId}
                  onChange={(event) => setPreferences((current) => ({ ...current, semesterId: event.target.value }))}
                >
                  {semesters.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                </select>
              </label>
              <label className="setting-item">
                <strong>首页近期任务数量</strong>
                <span>控制首页单屏中显示多少条待办。</span>
                <select
                  value={preferences.recentLimit}
                  onChange={(event) => setPreferences((current) => ({ ...current, recentLimit: Number(event.target.value) }))}
                >
                  <option value={3}>3 条</option>
                  <option value={4}>4 条</option>
                  <option value={5}>5 条</option>
                </select>
              </label>
              <label className="setting-item">
                <strong>任务页默认范围</strong>
                <span>决定进入任务页时优先看到的内容。</span>
                <select
                  value={preferences.taskView}
                  onChange={(event) => setPreferences((current) => ({ ...current, taskView: event.target.value as TaskView }))}
                >
                  <option value="pending">待完成作业</option>
                  <option value="homework">本学期全部作业</option>
                  <option value="all">本学期全部任务</option>
                </select>
              </label>
            </div>
            <div className="settings-note">偏好已自动保存在当前浏览器。时间统一按北京时间显示。</div>
            <Panel title="知识库管理" className="settings-panel">
              <p className="muted intro">解析新课件或同步新附件后，需要重建知识库索引才能被问答检索到。</p>
              <button
                className="primary"
                onClick={rebuildKnowledge}
                disabled={rebuilding}
                style={{ marginTop: 12 }}
              >
                {rebuilding ? "重建中，请稍候..." : "重新构建知识库索引"}
              </button>
              {rebuildResult && <div className="rebuild-result" style={{ marginTop: 10, padding: '10px 14px', borderRadius: 10, background: '#e8f5e9', color: '#2e7d32', fontSize: 13 }}>{rebuildResult}</div>}
            </Panel>

            <Panel title="LLM 模型设置" className="settings-panel">
              <p className="muted intro">配置 API 地址、密钥、模型等。保存后立即生效，无需重启。</p>
              {llmConfigLoading && <p className="muted">加载中...</p>}
              {llmConfig && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
                  <label className="field">
                    <span>API Base URL</span>
                    <input
                      type="text"
                      value={llmConfig.base_url ?? ""}
                      onChange={(e) => setLlmConfig({ ...llmConfig, base_url: e.target.value })}
                      placeholder="https://llmapi.paratera.com"
                    />
                  </label>
                  <label className="field">
                    <span>API Key</span>
                    <input
                      type="password"
                      value={llmConfig.api_key ?? ""}
                      onChange={(e) => setLlmConfig({ ...llmConfig, api_key: e.target.value })}
                      placeholder="sk-..."
                    />
                  </label>
                  <label className="field">
                    <span>模型名称</span>
                    <input
                      type="text"
                      value={llmConfig.model ?? ""}
                      onChange={(e) => setLlmConfig({ ...llmConfig, model: e.target.value })}
                      placeholder="deepseek-chat"
                    />
                  </label>
                  <div className="form-row">
                    <label className="field">
                      <span>超时时间（秒）</span>
                      <input
                        type="number"
                        value={llmConfig.timeout ?? 60}
                        onChange={(e) => setLlmConfig({ ...llmConfig, timeout: Number(e.target.value) })}
                        min={10} max={300}
                      />
                    </label>
                    <label className="field">
                      <span>最大 Token</span>
                      <input
                        type="number"
                        value={llmConfig.max_tokens ?? 3072}
                        onChange={(e) => setLlmConfig({ ...llmConfig, max_tokens: Number(e.target.value) })}
                        min={512} max={16384}
                      />
                    </label>
                  </div>
                  <button className="primary" onClick={saveLlmConfig} style={{ marginTop: 8 }}>
                    保存 LLM 配置
                  </button>
                  {llmConfigSaved && (
                    <div style={{ padding: '8px 12px', borderRadius: 8, background: '#e8f5e9', color: '#2e7d32', fontSize: 13 }}>
                      配置已保存，立即生效。
                    </div>
                  )}
                </div>
              )}
            </Panel>
          </Panel>
        )}
      </main>
    </div>
  );
}

function TaskCenter({
  tasks,
  selectedTask,
  selectedTaskId,
  taskView,
  onTaskViewChange,
  onSelect,
}: {
  tasks: AnyRecord[];
  selectedTask: AnyRecord | null;
  selectedTaskId: string;
  taskView: TaskView;
  onTaskViewChange: (view: TaskView) => void;
  onSelect: (task: AnyRecord) => void;
}) {
  return (
    <section className="task-workspace">
      <div className="task-workspace-header">
        <div>
          <h3>本学期任务</h3>
          <p className="muted">优先处理未完成作业，也可以查看全部作业和通知。</p>
        </div>
        <span className="task-count">{tasks.length} 条</span>
      </div>
      <div className="segmented-control" aria-label="任务范围">
        <button className={taskView === "pending" ? "active" : ""} onClick={() => onTaskViewChange("pending")}>待完成</button>
        <button className={taskView === "homework" ? "active" : ""} onClick={() => onTaskViewChange("homework")}>全部作业</button>
        <button className={taskView === "all" ? "active" : ""} onClick={() => onTaskViewChange("all")}>全部任务</button>
      </div>
      {!tasks.length ? (
        <div className="empty-state">当前范围内没有任务。</div>
      ) : (
        <div className="task-center-grid">
          <RecordList
            records={tasks}
            limit={tasks.length}
            selectedId={selectedTaskId}
            onSelect={onSelect}
          />
          <TaskDetail task={selectedTask} />
        </div>
      )}
    </section>
  );
}

function TaskDetail({ task }: { task: AnyRecord | null }) {
  if (!task) return null;
  const attachments = Array.isArray(task.attachments) ? task.attachments : [];
  return (
    <aside className="task-detail" aria-live="polite">
      <div className={`deadline-hero ${deadlineTone(task.ddl)}`}>
        <span>截止时间</span>
        <strong>{formatDeadline(task.ddl, false)}</strong>
      </div>
      <div className="task-detail-title">
        <span>{taskTypeLabel(task.task_type)}</span>
        <h4>{task.title ?? task.course_name ?? "未命名任务"}</h4>
        <p>{task.course_name ?? "未知课程"}</p>
      </div>
      <div className="task-detail-content">{recordDescription(task)}</div>
      {attachments.length > 0 && (
        <div className="attachment-list">
          <h5>作业附件</h5>
          {attachments.map((attachment: AnyRecord, index: number) => (
            <a
              key={`${attachment.download_url ?? attachment.name ?? index}`}
              href={apiUrl(`/api/tasks/${encodeURIComponent(taskRecordId(task))}/attachments/${index}`)}
              target="_blank"
              rel="noreferrer"
            >
              <span>{attachment.name ?? `附件 ${index + 1}`}</span>
              <small>打开附件</small>
            </a>
          ))}
        </div>
      )}
      <dl className="task-meta-list">
        {task.status && <div><dt>状态</dt><dd>{taskStatusLabel(task.status)}</dd></div>}
        {task.published_at && <div><dt>开放时间</dt><dd>{formatDateTime(task.published_at)}</dd></div>}
        <div><dt>数据来源</dt><dd>{sourceLabel(task.source)}</dd></div>
      </dl>
    </aside>
  );
}

function RecordList({
  records,
  empty = "暂无数据",
  limit = 8,
  selectedId,
  onSelect,
  showDeadline = false,
}: {
  records: AnyRecord[];
  empty?: string;
  limit?: number;
  selectedId?: string;
  onSelect?: (record: AnyRecord) => void;
  showDeadline?: boolean;
}) {
  if (!records.length) return <div className="empty-state">{empty}</div>;
  return (
    <div className="record-list">
      {records.slice(0, limit).map((record, index) => {
        const id = taskRecordId(record);
        return (
          <article
            key={`${record.raw_id ?? record.title ?? index}`}
            className={`${onSelect ? "clickable" : ""} ${selectedId === id ? "selected" : ""}`}
            onClick={() => onSelect?.(record)}
            tabIndex={onSelect ? 0 : undefined}
            onKeyDown={(event) => {
              if (onSelect && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                onSelect(record);
              }
            }}
          >
            {(showDeadline || onSelect || record.ddl) && (
              <div className="record-deadline-row">
                <span className={`deadline-pill ${deadlineTone(record.ddl)}`}>{formatDeadline(record.ddl)}</span>
                <span>{record.status ? taskStatusLabel(record.status) : taskTypeLabel(record.task_type)}</span>
              </div>
            )}
            <div className="record-title-row">
              <strong>{record.title ?? record.course_name ?? "未命名任务"}</strong>
              {record.course_name && <em>{record.course_name}</em>}
            </div>
            <p>{recordDescription(record)}</p>
          </article>
        );
      })}
    </div>
  );
}

function Panel({
  title,
  className = "",
  children,
}: {
  title: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function StatCard({ title, value, hint }: { title: string; value: number; hint: string }) {
  return (
    <div className="stat-card">
      <p>{title}</p>
      <strong>{value}</strong>
      <span>{hint}</span>
    </div>
  );
}

function HwTaskSidebar({ task }: { task: AnyRecord | null }) {
  return (
    <aside className="sidebar-panel">
      <h4>作业详情</h4>
      {task ? (
        <>
          <div className="sidebar-field">
            <span className="sidebar-label">标题</span>
            <strong>{task.title ?? "未命名作业"}</strong>
          </div>
          {task.course_name && (
            <div className="sidebar-field">
              <span className="sidebar-label">课程</span>
              <span>{task.course_name}</span>
            </div>
          )}
          {task.ddl && (
            <div className="sidebar-field">
              <span className="sidebar-label">截止时间</span>
              <span className={`deadline-pill ${deadlineTone(task.ddl)}`}>{formatDeadline(task.ddl)}</span>
            </div>
          )}
          {task.status && (
            <div className="sidebar-field">
              <span className="sidebar-label">状态</span>
              <span>{taskStatusLabel(task.status)}</span>
            </div>
          )}
          <div className="sidebar-field">
            <span className="sidebar-label">内容</span>
            <div className="sidebar-content">{recordDescription(task)}</div>
          </div>
          {Array.isArray(task.attachments) && task.attachments.length > 0 && (
            <div className="sidebar-field">
              <span className="sidebar-label">附件（{task.attachments.length} 个）</span>
              <div className="sidebar-attachments">
                {task.attachments.map((attachment: AnyRecord, index: number) => (
                  <a
                    key={attachment.download_url ?? attachment.name ?? index}
                    href={apiUrl(`/api/tasks/${encodeURIComponent(taskRecordId(task))}/attachments/${index}`)}
                    target="_blank"
                    rel="noreferrer"
                    className="attachment-link"
                  >
                    {attachment.name ?? `附件 ${index + 1}`}
                  </a>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="muted">选择一个作业后，作业信息将显示在这里。</p>
      )}
    </aside>
  );
}

function MaterialSidebar({
  material,
  materialId,
  focusedPage,
  course,
  onAsk,
  onPageFocus,
}: {
  material: AnyRecord | null;
  materialId: string;
  focusedPage: number | null;
  course: string;
  onAsk: (question: string, page?: number) => void;
  onPageFocus: (page: number | null) => void;
}) {
  const [fileMeta, setFileMeta] = useState<AnyRecord | null>(null);
  const [previewOpen, setPreviewOpen] = useState(true);
  const fileUrl = materialId ? apiUrl(`/api/materials/${encodeURIComponent(materialId)}/file`) : null;

  useEffect(() => {
    if (!materialId) {
      setFileMeta(null);
      return;
    }
    setPreviewOpen(true);
    let cancelled = false;
    apiGet<AnyRecord>(`/api/materials/${encodeURIComponent(materialId)}/chunks`)
      .then((data) => { if (!cancelled) setFileMeta(data.file ?? null); })
      .catch(() => { if (!cancelled) setFileMeta(null); });
    return () => { cancelled = true; };
  }, [materialId]);

  const typeLabel: Record<string, string> = { pdf: "PDF 文档", pptx: "PPT 演示文稿", docx: "Word 文档", image: "图片" };
  const canPreview = fileMeta && (fileMeta.material_type === "pdf" || fileMeta.material_type === "image" || String(fileMeta.material_type || "").startsWith("image"));

  function formatBytes(bytes: number | undefined): string {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatPublished(dateStr: string | undefined): string {
    if (!dateStr) return "";
    try {
      const d = new Date(dateStr);
      return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch {
      return dateStr.slice(0, 10);
    }
  }

  return (
    <aside className="sidebar-panel document-browser">
      <h4>{material ? "文件预览" : "文件信息"}</h4>
      {material ? (
        <>
          <div className="sidebar-field">
            <strong>{material.title ?? material.file_name ?? "未命名文件"}</strong>
            <span className="file-meta">
              {material.course_name ?? course ?? ""}
              {fileMeta?.material_type ? ` · ${typeLabel[fileMeta.material_type] ?? fileMeta.material_type?.toUpperCase()}` : ""}
              {(fileMeta?.bytes ?? 0) > 0 ? ` · ${formatBytes(fileMeta!.bytes)}` : ""}
              {fileMeta?.published_at ? ` · ${formatPublished(fileMeta!.published_at)}` : ""}
              {(fileMeta?.total_pages ?? 0) > 0 ? ` · ${fileMeta!.total_pages} 页` : ""}
            </span>
          </div>

          {fileUrl && (
            <div className="sidebar-field">
              <div className="preview-controls">
                {canPreview && (
                  <button
                    className="browser-toggle"
                    onClick={() => setPreviewOpen((v) => !v)}
                  >
                    {previewOpen ? "收起文件预览 ▴" : "展开文件预览 ▾"}
                  </button>
                )}
                <a
                  className="open-file-link"
                  href={fileUrl}
                  target="_blank"
                  rel="noreferrer"
                  style={canPreview ? {} : { display: "block", padding: "8px 10px", border: "1px solid #315edb", borderRadius: 8, textAlign: "center", fontWeight: 600, fontSize: 13 }}
                >
                  {canPreview ? "在新标签页打开" : "在新标签页打开（此格式不支持内嵌预览）"}
                </a>
              </div>
            </div>
          )}

          {previewOpen && fileUrl && canPreview && (
            <div className="sidebar-preview-frame">
              <iframe
                src={fileUrl}
                className="preview-iframe"
                title="文件预览"
              />
            </div>
          )}

          <div className="sidebar-field">
            <span className="sidebar-label">快捷提问</span>
            <div className="quick-ask-buttons">
              <button className="quick-ask-btn" onClick={() => { onPageFocus(null); onAsk("总结这份文件的核心内容"); }}>
                总结核心内容
              </button>
              <button className="quick-ask-btn" onClick={() => { onPageFocus(null); onAsk("这份文件涉及哪些重要概念？"); }}>
                提取重要概念
              </button>
              {focusedPage !== null && (
                <button className="quick-ask-btn" onClick={() => onAsk(`第 ${focusedPage} 页讲了什么？`, focusedPage)}>
                  仅提问第 {focusedPage} 页
                </button>
              )}
            </div>
          </div>
        </>
      ) : (
        <p className="muted">指定某个文件后，可以在这里预览原文件并一键提问。</p>
      )}
    </aside>
  );
}

function ThinkingBanner() {
  return (
    <div className="thinking-banner">
      <span className="spinner" />
      <div><strong>正在分析</strong><p>检索相关任务和课程资料，请稍候...</p></div>
    </div>
  );
}

function ResultMeta({ result }: { result: AnyRecord }) {
  const retrieved = result.retrieved;
  return (
    <div className="result-meta">
      <span className={`status-tag ${result.status}`}>{result.status === "ready" ? "已生成" : "调用失败"}</span>
      {retrieved && (
        <span className="retrieved-info">
          任务与通知 {retrieved.a_module ?? 0} 条 · 课程资料 {retrieved.c_module ?? 0} 条
          {retrieved.queries_used?.length > 0 && <> · 搜索词：{retrieved.queries_used.join("、")}</>}
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
      {citations.map((citation, index) => (
        <div key={index} className="citation-item">
          <div className="citation-title">{citation.title}</div>
          <div className="citation-snippet">{citation.snippet}</div>
          <div className="citation-source">{citation.source}</div>
        </div>
      ))}
    </div>
  );
}

function HomeworkResult({ result }: { result: AnyRecord }) {
  return (
    <div className="answer-card">
      <ResultMeta result={result} />
      {result.outline?.length > 0 && <ResultList title="解题步骤" items={result.outline} ordered />}
      {result.draft && <div className="section-block"><h4>思路草稿</h4><div className="answer-body">{result.draft}</div></div>}
      {result.pitfalls?.length > 0 && <ResultList title="常见注意点" items={result.pitfalls} />}
      {result.checklist?.length > 0 && <ResultList title="自查清单" items={result.checklist} />}
      <CitationList citations={result.citations} />
    </div>
  );
}

function ResultList({ title, items, ordered = false }: { title: string; items: string[]; ordered?: boolean }) {
  const content = items.map((item, index) => <li key={index}>{item}</li>);
  return <div className="section-block"><h4>{title}</h4>{ordered ? <ol>{content}</ol> : <ul>{content}</ul>}</div>;
}

function loadPreferences(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem("learning-assistant-preferences") ?? "{}");
    return { ...defaultPreferences, ...saved };
  } catch {
    return defaultPreferences;
  }
}

function extractCourses(records: AnyRecord[]): string[] {
  return [...new Set(
    records
      .map((record) => String(record.course_name ?? "").trim())
      .filter((name) => name && name !== "Unknown Course"),
  )].sort((left, right) => left.localeCompare(right, "zh"));
}

function recordBelongsToSemester(
  record: AnyRecord,
  semester: (typeof semesters)[number],
): boolean {
  const start = new Date(`${semester.start}T00:00:00+08:00`).getTime();
  const end = new Date(`${semester.end}T23:59:59+08:00`).getTime();
  const candidates = [record.published_at, record.ddl, record.starts_at];
  if (record.source !== "learn") candidates.push(record.created_at);
  return candidates.some((value) => {
    if (typeof value !== "string" || !value) return false;
    const timestamp = new Date(value).getTime();
    return !Number.isNaN(timestamp) && timestamp >= start && timestamp <= end;
  });
}

function taskRecordId(record: AnyRecord): string {
  return String(
    record.raw_id
      ?? record.id
      ?? `${record.source ?? "task"}:${record.task_type ?? ""}:${record.course_name ?? ""}:${record.title ?? ""}`,
  );
}

function materialRecordId(record: AnyRecord): string {
  return String(record.material_id ?? record.file_hash ?? record.source_file ?? record.chunk_id ?? "");
}

function recordDescription(record: AnyRecord): string {
  const content = record.content ?? record.text ?? record.message;
  if (typeof content === "string" && content.trim()) return content.trim();
  if (record.task_type === "homework") {
    return Array.isArray(record.attachments) && record.attachments.length
      ? "暂无文字说明，请查看作业附件。"
      : "该作业暂未提供文字说明。";
  }
  return "该任务暂未提供详细说明。";
}

function taskTypeLabel(value: unknown): string {
  const labels: Record<string, string> = {
    homework: "课程作业",
    notice: "课程通知",
    questionnaire: "课程问卷",
    discussion: "课程讨论",
    exam: "考试安排",
    file: "课程文件",
  };
  return labels[String(value ?? "")] ?? "课程任务";
}

function taskStatusLabel(value: unknown): string {
  const labels: Record<string, string> = {
    unsubmitted: "未提交",
    submitted_ungraded: "已提交，待批改",
    graded: "已批改",
  };
  return labels[String(value ?? "")] ?? String(value ?? "状态未知");
}

function sourceLabel(value: unknown): string {
  const labels: Record<string, string> = {
    learn: "网络学堂",
    mail: "课程邮箱",
    jwch: "教务系统",
  };
  return labels[String(value ?? "")] ?? "课程平台";
}

function formatDeadline(value: unknown, withPrefix = true): string {
  if (typeof value !== "string" || !value.trim()) return "无截止时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const formatted = date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return withPrefix ? `截止 ${formatted}` : formatted;
}

function formatDateTime(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function deadlineTone(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "no-deadline";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "no-deadline";
  const difference = timestamp - Date.now();
  if (difference < 0) return "overdue";
  if (difference <= 1000 * 60 * 60 * 24 * 3) return "soon";
  return "upcoming";
}

function isFutureDeadline(value: unknown): boolean {
  return typeof value === "string" && new Date(value).getTime() >= Date.now();
}

async function learnSyncTimestamp(): Promise<string> {
  const status = await apiGet<AnyRecord>("/api/sync/status");
  return String(status.items?.find((item: AnyRecord) => item.channel === "learn")?.last_synced_at ?? "");
}

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
