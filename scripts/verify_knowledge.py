"""Smoke checks for the C-module knowledge base and retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.knowledge_base import KnowledgeBase


DEMO_JSONL = ROOT / "storage" / "demo_material_chunks.jsonl"
INDEX_DIR = ROOT / "storage" / "test_knowledge_index"


def main() -> int:
    """Verify C-module indexing, keyword search, and status reporting."""
    failures = 0

    # 1. Build index from Demo JSONL
    print("=== C Module Verification ===")
    print(f"Building index from: {DEMO_JSONL}")
    kb = KnowledgeBase(index_dir=str(INDEX_DIR), chunks_jsonl=str(DEMO_JSONL))
    status = kb.build()
    print(f"  Status: {status.status}")
    print(f"  Indexed chunks: {status.indexed_chunks}")
    print(f"  Index types: {status.index_types}")
    print(f"  Message: {status.message}")

    if status.status != "ready":
        print("[FAIL] Index build did not return ready status")
        failures += 1
    elif status.indexed_chunks == 0:
        print("[FAIL] No chunks were indexed")
        failures += 1
    else:
        print(f"[OK] Index built with {status.indexed_chunks} chunks")

    # 2. Status check
    status_result = kb.status()
    print(f"\nStatus API: {json.dumps(status_result, ensure_ascii=False, indent=2)}")
    if status_result.get("status") != "ready":
        print("[FAIL] Status not ready")
        failures += 1
    else:
        print("[OK] Status reports ready")

    # 3. Keyword search
    print("\n--- Keyword Search ---")
    kw_results = kb.search(query="课程导论", top_k=5, mode="keyword")
    print(f"  Results: {len(kw_results)}")
    for r in kw_results:
        print(f"    [{r['score']:.4f}] {r['title']} - {r['text'][:60]}...")
    if not kw_results:
        print("[FAIL] Keyword search returned no results")
        failures += 1
    else:
        print("[OK] Keyword search returned results")

    # 4. Vector search (optional)
    print("\n--- Vector Search ---")
    vec_results = kb.search(query="课程导论", top_k=5, mode="vector")
    print(f"  Results: {len(vec_results)}")
    if vec_results:
        print("[OK] Vector search returned results (sentence-transformers available)")
    else:
        print("[SKIP] Vector search unavailable (sentence-transformers not installed)")

    # 5. Hybrid search
    print("\n--- Hybrid Search ---")
    hybrid_results = kb.search(query="课程导论", top_k=5, mode="hybrid")
    print(f"  Results: {len(hybrid_results)}")
    for r in hybrid_results:
        print(f"    [{r['score']:.4f}] {r['title']} - {r['text'][:60]}...")
    if not hybrid_results:
        print("[FAIL] Hybrid search returned no results")
        failures += 1
    else:
        print("[OK] Hybrid search returned results")

    # 6. Metadata filter
    print("\n--- Metadata Filter (course_name='通用课程') ---")
    filtered = kb.search(query="课程", course_name="通用课程", top_k=5)
    print(f"  Results: {len(filtered)}")
    for r in filtered:
        print(f"    [{r['score']:.4f}] course={r['course_name']} - {r['text'][:60]}...")

    # 7. Load from disk
    print("\n--- Load from Disk ---")
    kb2 = KnowledgeBase(index_dir=str(INDEX_DIR), chunks_jsonl=str(DEMO_JSONL))
    loaded = kb2.load()
    if loaded:
        print(f"[OK] Index loaded from disk: {kb2.status()}")
    else:
        print("[FAIL] Failed to load index from disk")
        failures += 1

    # 8. Reloaded search
    reloaded_results = kb2.search(query="Demo", top_k=3)
    print(f"\n--- Reloaded Search ---")
    print(f"  Results: {len(reloaded_results)}")
    if reloaded_results:
        print("[OK] Reloaded index returns search results")
    else:
        print("[FAIL] Reloaded search returned no results")
        failures += 1

    # Summary
    print(f"\n{'=' * 40}")
    if failures:
        print(f"[FAIL] {failures} check(s) failed")
    else:
        print("[OK] All C-module checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
