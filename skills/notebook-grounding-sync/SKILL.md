---
name: notebook-grounding-sync
description: >-
  Syncs facts, model evaluations, benchmarks, and project documentation into Gemini Notebook
  (NotebookLM MCP) as authoritative ground truth sources. Enables continuous knowledge base expansion.
---

# Notebook Grounding & Sync Skill (Gemini Notebook / HQ)

Maintains the authoritative Ground Truth database in Gemini Notebook (NotebookLM). Automatically packages newly discovered technical facts, verified benchmarks, and architecture decisions into structured sources and synchronizes them with the designated notebook.

## Key Notebooks
- **2026年AIモデル総合ランキングと評価ガイド**: `7fcbc905-75d1-4953-8afc-6447afa487ef`
- **LLM個人開発・発信戦略まとめ**: `ca17f589-3c10-4404-a0a9-1ccd048f1d51`

## Workflow

### 1. Identify Target Notebook
If not explicitly provided by the user, query existing notebooks using `notebook_list` to find the most relevant notebook ID.

### 2. Prepare Structured Ground Truth Content
Format the content with clear section headers, dates, and quantitative citations:
```markdown
# [検証レポート] タイトル (YYYY-MM-DD)

## 概要と検証目的
...

## 定量的ベンチマーク・仕様
- スコア / 指標: 
- 料金・コスト: 
- 出典 / ソースURL: 

## 技術的考察と結論
...
```

### 3. Add Source via `gemini-notebook-mcp`
Invoke `source_add` with the following parameters:
- `notebook_id`: Target notebook UUID
- `source_type`: `"text"` (or `"file"` / `"url"`)
- `title`: `"[検証] <簡潔なソース名> (<日付>)"`
- `text`: Structured markdown body
- `wait`: `true`

### 4. Verification & Grounding Check
Immediately run a test query with `notebook_query` on the target notebook to verify that the newly added information can be reliably cited and retrieved.

## Rules
- **No Speculation**: Only sync verified, deterministic facts and benchmark numbers.
- **Explicit Versioning**: Always tag model names with explicit versions and timestamps (e.g. `2026-11-04`).
