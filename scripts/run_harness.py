"""Architecture harness for the Course Agent Collector.

This script intentionally uses only the Python standard library.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TASK_FILES = [
    "tasks.md",
    "docs/tasks.md",
    "config/tasks.md",
    "src/tasks.md",
    "src/adapters/tasks.md",
    "src/parsers/tasks.md",
    "src/models/tasks.md",
    "src/materials/tasks.md",
    "storage/tasks.md",
    "logs/tasks.md",
    "scripts/tasks.md",
    "tests/tasks.md",
]
PYTHON_DIRS = ["src", "scripts"]
FORBIDDEN_PARSER_IMPORTS = {"requests", "imaplib", "httpx", "urllib3", "os", "sqlite3"}
SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*=\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"(?i)(cookie|authorization)\s*:\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"\b\d{10}\b"),
]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".env"}
SAFE_SECRET_MARKERS = {
    "LEARN_PASSWORD",
    "MAIL_PASSWORD",
    "JWCH_PASSWORD",
    "passwords",
    "password",
    "API Key",
    "api keys",
}


def iter_files(*roots: str) -> list[Path]:
    files: list[Path] = []
    for name in roots:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        elif path.exists():
            files.extend(item for item in path.rglob("*") if item.is_file())
    return files


def read_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported_modules(path: Path) -> set[str]:
    tree = read_ast(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
            modules.add(node.module)
    return modules


def check_task_files() -> list[str]:
    errors: list[str] = []
    for relative in TASK_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing task file: {relative}")
    return errors


def check_compile() -> list[str]:
    errors: list[str] = []
    for path in iter_files(*PYTHON_DIRS, "main.py"):
        if path.suffix != ".py":
            continue
        relative = path.relative_to(ROOT)
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            errors.append(f"compile failed for {relative}: {exc.msg} at line {exc.lineno}")
    return errors


def check_parser_no_network() -> list[str]:
    errors: list[str] = []
    parser_dir = ROOT / "src" / "parsers"
    if not parser_dir.exists():
        return errors
    for path in parser_dir.rglob("*.py"):
        forbidden = imported_modules(path) & FORBIDDEN_PARSER_IMPORTS
        if forbidden:
            errors.append(f"parser imports network module {sorted(forbidden)}: {path.relative_to(ROOT)}")
    return errors


def check_adapter_no_parser_imports() -> list[str]:
    errors: list[str] = []
    adapter_dir = ROOT / "src" / "adapters"
    if not adapter_dir.exists():
        return errors
    for path in adapter_dir.rglob("*.py"):
        modules = imported_modules(path)
        if any(module == "src.parsers" or module.startswith("src.parsers.") for module in modules):
            errors.append(f"adapter imports parser layer: {path.relative_to(ROOT)}")
    return errors


def check_adapter_no_model_imports() -> list[str]:
    errors: list[str] = []
    adapter_dir = ROOT / "src" / "adapters"
    if not adapter_dir.exists():
        return errors
    for path in adapter_dir.rglob("*.py"):
        modules = imported_modules(path)
        if any(module == "src.models" or module.startswith("src.models.") for module in modules):
            errors.append(f"adapter imports model layer: {path.relative_to(ROOT)}")
    return errors


def is_safe_secret_context(line: str) -> bool:
    if "your_" in line or "example.invalid" in line:
        return True
    return any(marker in line for marker in SAFE_SECRET_MARKERS) and "os.getenv" in line


def check_sensitive_placeholders() -> list[str]:
    errors: list[str] = []
    for path in iter_files("src", "scripts", "tests", "config", "docs", "main.py", "requirements.txt", ".gitignore"):
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if is_safe_secret_context(line):
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    errors.append(f"sensitive-looking value in {path.relative_to(ROOT)}:{line_number}")
                    break
    return errors


def check_mail_uid_incremental() -> list[str]:
    errors: list[str] = []
    try:
        from src.adapters.mail_adapter import MailAdapter
        from src.adapters.base_adapter import AdapterConfig
    except Exception as exc:
        return [f"mail adapter import failed: {exc}"]

    class FakeImap:
        def login(self, user: str, password: str):
            return "OK", [b"logged in"]

        def select(self, mailbox: str, readonly: bool = True):
            return "OK", [b"3"]

        def uid(self, command: str, *args: str):
            if command == "SEARCH":
                return "OK", [b"2 3"]
            if command == "FETCH":
                uid = args[0]
                return "OK", [(b"RFC822", f"Subject: probe {uid}\r\n\r\nbody".encode("utf-8"))]
            return "NO", []

        def logout(self):
            return "OK", [b"logout"]

    config = AdapterConfig(
        source="mail",
        base_url="example.invalid",
        username="user",
        password="x",
    )
    adapter = MailAdapter(config, imap_client=FakeImap())
    payloads = adapter.fetch_raw(limit=5, since_uid=1)
    raw_ids = [payload.raw_id for payload in payloads]
    if raw_ids != ["mail_uid_2", "mail_uid_3"]:
        errors.append(f"unexpected mail raw_ids: {raw_ids}")
    if any(payload.content_type != "message/rfc822" for payload in payloads):
        errors.append("mail payload content_type is not message/rfc822")
    return errors


def check_pipeline_sync_state_contract() -> list[str]:
    errors: list[str] = []
    pipeline_path = ROOT / "src" / "pipeline.py"
    text = pipeline_path.read_text(encoding="utf-8")
    required_fragments = [
        "CREATE TABLE IF NOT EXISTS sync_state",
        "def get_state(",
        "def set_state(",
        "def get_int_state(",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            errors.append(f"pipeline sync state fragment missing: {fragment}")
    return errors


def check_jwch_parser_contract() -> list[str]:
    errors: list[str] = []
    try:
        from src.parsers.jwch_html import JwchHtmlParser
    except Exception as exc:
        return [f"jwch parser import failed: {exc}"]

    exam_html = """
    <html><body>
    <table><tr><td>2025-2026学年 春 考试安排</td></tr></table>
    <table>
      <tr><th>开课系</th><th>课程号</th><th>课序号</th><th>课程名</th><th>课程分类</th><th>教师</th><th>人数</th><th>考试日期</th><th>考场</th></tr>
      <tr><td>计算机系</td><td>30240043</td><td>0</td><td>程序设计基础</td><td>本科生</td><td>张三</td><td>30</td><td>06.22一 下午</td><td>六教6A201</td></tr>
    </table>
    </body></html>
    """
    exam_records = JwchHtmlParser().parse(exam_html, {"raw_id": "fixture_exam", "url": "bks_ksSearch"})
    if len(exam_records) != 1:
        errors.append(f"unexpected jwch exam record count: {len(exam_records)}")
    elif exam_records[0].schedule_type != "exam":
        errors.append(f"unexpected jwch exam schedule_type: {exam_records[0].schedule_type}")
    elif exam_records[0].starts_at.isoformat() != "2026-06-22T14:00:00+08:00":
        errors.append(f"unexpected jwch exam starts_at: {exam_records[0].starts_at.isoformat()}")

    schedule_html = """
    <html><body><table>
      <tr><th></th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th><th>星期五</th><th>星期六</th><th>星期日</th></tr>
      <tr><td>第1节</td><td>程序设计基础 地点: 六教6A201</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
    </table></body></html>
    """
    schedule_records = JwchHtmlParser().parse(schedule_html, {"raw_id": "fixture_schedule"})
    if len(schedule_records) != 1:
        errors.append(f"unexpected jwch schedule record count: {len(schedule_records)}")
    elif schedule_records[0].schedule_type != "class":
        errors.append(f"unexpected jwch schedule schedule_type: {schedule_records[0].schedule_type}")
    elif schedule_records[0].location != "六教6A201":
        errors.append(f"unexpected jwch schedule location: {schedule_records[0].location}")
    js_schedule_html = """
    <script>
    var strHTML="";
    strHTML="";
    strHTML1 = "";
    strHTML1 += "；张三";
    strHTML1 += "；必修";
    strHTML1 += "；全周";
    strHTML1 +="；六教6C102"
    strHTML += "<a class='mainHref' href='js.vjsKcbBs.do?m=showToXs&p_id=fake-course-id' target='_blank'>";
    strHTML += "<span onmouseover=\\"return overlib('x');\\" onmouseout=\\"return nd();\\">面向对象程序设计基础";
    strHTML += "</span>";
    strHTML += "</a>";
    document.getElementById('a3_5').innerHTML += strHTML+"<br>";
    </script>
    """
    js_records = JwchHtmlParser().parse(js_schedule_html, {"raw_id": "fixture_schedule_js"})
    if len(js_records) != 1:
        errors.append(f"unexpected jwch js schedule record count: {len(js_records)}")
    elif js_records[0].course_name != "面向对象程序设计基础":
        errors.append(f"unexpected jwch js schedule course: {js_records[0].course_name}")
    elif js_records[0].starts_at.isoformat() != "1970-01-09T09:50:00+08:00":
        errors.append(f"unexpected jwch js schedule starts_at: {js_records[0].starts_at.isoformat()}")
    return errors


def check_material_parser_contract() -> list[str]:
    errors: list[str] = []
    try:
        from src.materials.pipeline import (
            build_parse_report,
            load_existing_file_hashes,
            parse_material_paths,
            parse_material_paths_with_report,
            write_chunks_jsonl,
        )
    except Exception as exc:
        return [f"material parser import failed: {exc}"]

    fixture = ROOT / "tests" / "fixtures" / "material_sample.md"
    chunks, parse_errors = parse_material_paths([fixture], chunk_chars=300, overlap_chars=30)
    if parse_errors:
        errors.append(f"unexpected material parse errors: {parse_errors}")
    if len(chunks) != 1:
        errors.append(f"unexpected material chunk count: {len(chunks)}")
    elif chunks[0].material_type != "markdown":
        errors.append(f"unexpected material type: {chunks[0].material_type}")
    elif "机器学习" not in chunks[0].text:
        errors.append("material chunk text missing expected course content")
    elif not chunks[0].chunk_id.startswith("material_"):
        errors.append(f"material chunk_id is not stable B-module id: {chunks[0].chunk_id}")
    elif len(chunks[0].text_hash) != 64:
        errors.append("material text_hash is not a SHA-256 hex digest")

    reported_chunks, reported_errors, file_reports = parse_material_paths_with_report(
        [fixture], chunk_chars=300, overlap_chars=30
    )
    if reported_errors:
        errors.append(f"unexpected material report errors: {reported_errors}")
    if not file_reports or file_reports[0].status != "parsed":
        errors.append("material parse report missing parsed file status")
    elif file_reports[0].chunks != len(reported_chunks):
        errors.append("material parse report chunk count does not match parsed chunks")

    report = build_parse_report(
        inputs=[fixture],
        output="storage/material_chunks.jsonl",
        chunk_chars=300,
        overlap_chars=30,
        incremental=False,
        chunks_parsed=len(reported_chunks),
        chunks_written=len(reported_chunks),
        errors=[],
        file_reports=file_reports,
    )
    if report.files_input != 1 or report.files_parsed != 1:
        errors.append("material aggregate report counts are incorrect")

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "chunks.jsonl"
        written = write_chunks_jsonl(reported_chunks, output)
        appended = write_chunks_jsonl(reported_chunks, output, append=True, dedupe=True)
        if written != len(reported_chunks):
            errors.append("material initial JSONL write count is incorrect")
        if appended != 0:
            errors.append("material append dedupe did not suppress duplicate chunks")
        if not load_existing_file_hashes(output):
            errors.append("material existing file hash loader returned no hashes")
    return errors


def run_check(name: str, checker) -> list[str]:
    errors = checker()
    status = "PASS" if not errors else "FAIL"
    print(f"[{status}] {name}")
    for error in errors:
        print(f"  - {error}")
    return errors


def main() -> int:
    checks = [
        ("tasks files exist", check_task_files),
        ("python files compile", check_compile),
        ("parsers avoid network imports", check_parser_no_network),
        ("adapters avoid parser imports", check_adapter_no_parser_imports),
        ("adapters avoid model imports", check_adapter_no_model_imports),
        ("sensitive placeholders are safe", check_sensitive_placeholders),
        ("mail UID incremental harness", check_mail_uid_incremental),
        ("pipeline sync state contract", check_pipeline_sync_state_contract),
        ("jwch parser contract", check_jwch_parser_contract),
        ("material parser contract", check_material_parser_contract),
    ]
    errors: list[str] = []
    for name, checker in checks:
        errors.extend(run_check(name, checker))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
