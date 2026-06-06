# Materials Tasks

## Goal
Convert local course files into clean, metadata-rich text chunks that the retrieval module can index without understanding file formats.

## Boundaries
- This module only reads local files and optional local JSONL/manifest metadata.
- It must not fetch Learn, Mail, JWCH, or any remote resource.
- It must not build vector indexes, answer questions, or summarize final responses.
- It must preserve course, file, page, slide, and source metadata whenever available.

## Task List
- Extract text from Markdown and plain text using encoding fallbacks.
- Extract page text from PDFs.
- Fall back to local OCR for low-text PDF pages when optional OCR dependencies are available.
- Extract paragraphs/tables from DOCX files.
- Extract slide text and table text from PPTX files.
- Provide an image OCR extractor behind optional local OCR dependencies.
- Provide audio/video ASR extractors behind an optional local faster-whisper backend.
- Clean extracted text and split it into bounded chunks.
- Produce stable chunk IDs and text hashes for downstream incremental indexing.
- Support incremental parsing and duplicate suppression for previously parsed files.
- Write a per-file parse report for validation and demos.
- Output `MaterialChunk` JSONL records for the retrieval module.

## Acceptance Harness
- `python scripts/run_harness.py` must compile this package.
- `python scripts/parse_materials.py --input <file-or-dir> --dry-run` should report parsed chunk counts without network access.
- Output records must include `chunk_id`, `source_file`, `file_hash`, `material_type`, `course_name`, `title`, `chunk_index`, `text_hash`, and `text`.

## Risks
- PDF extraction quality depends on whether the PDF contains selectable text.
- OCR requires local Tesseract installation in addition to Python packages.
- ASR requires optional `faster-whisper` and FFmpeg, and can be slow on CPU.
- Legacy `.doc` and `.ppt` files are not parsed directly; convert them to `.docx` or `.pptx` first.
