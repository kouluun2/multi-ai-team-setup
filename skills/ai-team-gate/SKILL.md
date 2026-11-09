---
name: ai-team-gate
description: >-
  1-HQ + 5-Tier Synergy Gatekeeper: Enforces deterministic quality gates (mypy/tsc/ruff/pytest) and multi-model synergy.
  Integrates Gemini Notebook (Ground Truth), Gemini 3.8 Flash High (executor), DeepSeek V4.1 Flash (1st line fast logic/audit),
  DeepSeek V4 Pro (2nd line 1.6T heavy algorithm/refactoring), Claude Opus 5.5 / GPT-6.1 Sol (top-tier diff audit),
  Gemini 4 Argon (Chief Architect), and RTX 4070 SUPER (0ms tab completion / local R1 fallback).
---

# AI Team Gatekeeper Skill

Enforces the 1-HQ + 5-Tier Synergy and Deterministic Verification Pipeline.

## Capabilities & Commands
Execute via PowerShell:
```powershell
# 1. 変更差分だけを抽出してトップ層敵対的レビュー（Claude Opus 5.5 / GPT-6.1 Sol / Gemini 4 Argon）
python ai_team_gate.py review [--model claude-opus-5-5|gpt-6-1-sol|gemini-4-argon] [--path <repo_path>]

# 1-a. レビュー差分パッケージの事前確認（API呼び出しなし・ドライラン）
python ai_team_gate.py review --dry-run [--path <repo_path>]

# 1-b. 認証・セキュリティ等重大変更時に Claude の深層思考（Extended Thinking）で徹底監査
python ai_team_gate.py review --deep [--thinking-budget 2048] [--path <repo_path>]

# 2. アーキテクチャ設計・タスク分解（Parent=Flash 時は DeepSeek V4 Pro を Virtual Commander として使用、または Gemini 4 Argon）
#    ※Parent が Flash の場合、まず architect --model deepseek-v4-pro で仕様策定とサブエージェント向け原子タスク分解を実行
python ai_team_gate.py architect "<task_or_architecture_spec>" [--model deepseek-v4-pro|gemini-4-argon] [-f <spec_file>]

# 3. 高速ロジック・並行処理バグ・1次差分監査（DeepSeek V4.1 Flash, 552B MoE, $0.30/$1.20 per 1M）
python ai_team_gate.py logic "<problem_description>" --model deepseek-flash

# 3-b. 難関競技アルゴリズム・大規模複数ファイル改修・深層バグ（DeepSeek V4 Pro, 1.6T MoE, $0.43/$0.87 per 1M）
python ai_team_gate.py logic "<problem_description>" --model deepseek-v4-pro

# 3-c. オフライン・完全無料フォールバック（自宅ローカルGPU: DeepSeek R1 14B）
# （※API未設定時または障害時に自動フォールバック）

# 4. 機械的品質検証を一括実行（失敗時は exit code 1 で停止）
#    Python (mypy, ruff, pytest) および Node/TS (tsc, eslint, npm test, --build で npm run build) を自動検出
python ai_team_gate.py check [--path <repo_path>] [--build]
```

## Rules:
- **Strict Zero-Code Policy (Parent Agent)**: Direct code editing (`write_file`/`edit_file` on code files: `.py`, `.ts`, `.ps1`, etc.) by the Parent Agent is strictly prohibited with zero exceptions. All coding and debug loops must be delegated to subagents.
- **Dynamic On-Demand Delegation**: Avoid fixed squads; spawn 1 to N subagents by concern. Mandate `Workspace: 'share'` or branch isolation for parallel subagents to prevent file collisions. Builder and verifier must be separate concerns (never self-approve).
- **Clean Rollback Before Escalation**: Perform `git restore .` to last known good state on >=2 test/debug failures before escalating to DeepSeek V4 Pro or Gemini 4 Argon.
- **Destructive Command & Secret Safeguards**: Strictly forbid destructive commands (`rm -rf`, `git push --force`, dropping tables/DBs, `kill -9`) without explicit USER confirmation. Never expose secrets/keys in logs or diffs.
- **Ground Truth First**: Never speculate model lifecycles or versions; always query Gemini Notebook MCP before claims.
- **Pre-Approved Logic Execution**: `deepseek-flash` and `deepseek-v4-pro` are pre-approved for autonomous debugging loops and logic validation.
- **Diff-Only Pinpoint**: Never feed the entire codebase to external review APIs. Only send Git diff + interface signatures.
- **Deterministic Quality**: Always run mechanical checks (`ai_team_gate.py check`) and pass 0 errors before completion.
- **Approval for Top-Tier Audits**: Offer Claude Opus 5.5 / GPT-6.1 Sol / DeepSeek audit to USER upon completing major features.

## Model Roster (reference data, 2026-11)

| Model | CLI / Model ID | Role | Benchmarks / Specs | Price (per 1M tokens) | Deployment |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini Notebook** | `gemini-notebook-mcp` | HQ (Ground Truth) | Authoritative specs, benchmarks & docs | Included | Google Cloud / MCP |
| **Gemini 4 Argon** | `gemini-4-argon` | Commander (Parent / Chief Architect) | High-level architecture, interface/type design, milestone acceptance | Standard Cloud API | Proprietary Cloud API |
| **Gemini 3.8 Flash High** | `Model: 'flash'` | Executors (Parallel subagents) | 131–285 tok/s | Free / Unlimited | Proprietary Cloud API |
| **DeepSeek V4.1 Flash** | `deepseek-flash` | 1st Defense Line (Fast logic & diff audit) | 552B MoE, 170–274 tok/s, GPQA 90.9%, Terminal-Bench 90.6% | $0.30 in / $1.20 out | Cloud API |
| **DeepSeek V4 Pro** | `deepseek-v4-pro` | Virtual Commander (when Parent=Flash) & 2nd Defense Line (Heavy algorithms/refactoring) | 1.6T MoE, LiveCodeBench 93.5%, SWE-bench Verified 80.6% | $0.43 in / $0.87 out (28% cheaper out than Flash) | Cloud API |
| **Claude Opus 5.5** | `claude-opus-5-5` | Top-Tier Red Team (Security/code audit) | SWE-bench Pro 89.9% | $4 in / $20 out | Proprietary Cloud API |
| **GPT-6.1 Sol** | `gpt-6-1-sol` | Top-Tier Red Team (Abstract/math reasoning) | ARC-AGI-2 94.2% | $2 in / $10 out | Proprietary Cloud API |
| **RTX 4070 SUPER GPU** | `local-autocomplete-fast` / `local-reasoner` | Local Autocomplete & Offline Fallback | Qwen 2.5 Coder 1.5B (<20ms tab completion), DeepSeek R1 Distill 14B | Local 0-cost | Open-weights / Local GPU |
