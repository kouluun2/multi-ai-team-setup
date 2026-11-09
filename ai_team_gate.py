#!/usr/bin/env python3
"""
AI Team Gatekeeper (5-Tier Synergy)
Usage:
    python ai_team_gate.py review [--model claude-opus-5-5|claude-sonnet-5-5|gemini-4-argon|gpt-6-1-sol] [--path <repo_path>] [--dry-run]
    python ai_team_gate.py architect ["<spec_or_escalation_problem>"] [--file <prompt_file>] [--model deepseek-v4-pro|gemini-4-argon]
    python ai_team_gate.py logic ["<problem_description>"] [--file <prompt_file>] [--model deepseek-flash|deepseek-v4-pro]
    python ai_team_gate.py check [--path <repo_path>] [--build]

Commands:
    review:    Git diff を抽出して Claude (Opus 5.5 / Sonnet 5.5), Gemini 4 Argon, または GPT-6.1 Sol に敵対的コードレビューを依頼
    architect: 初期アーキテクチャ設計・仕様策定や、Flash スタック時の高難度エスカレーション（DeepSeek V4 Pro 仮想Commander / Gemini 4 Argon）
    logic:     難解アルゴリズム・並行処理バグを DeepSeek V4.1 Flash (または V4-Pro, ローカル R1) に解かせる
    check:     静的型チェック・Linter・テストの機械的ゲートキーパーを一括実行

Exit Codes:
    0: PASS (All executed checks passed, diff collected, or operation succeeded)
    1: GATE FAIL (One or more quality checks failed)
    2: Argparse usage error (invalid command or missing required argument)
    3: API / network failure (API HTTP error, connection refused, or timeout)
    4: INCONCLUSIVE (Project detected, but zero checks executed / all skipped)
"""

import os
import sys
import json
import shutil
import fnmatch
import re
import subprocess
import argparse
import urllib.request
import urllib.error
from typing import Any, Optional

# Exit code constants
EXIT_PASS = 0
EXIT_GATE_FAIL = 1
EXIT_USAGE_ERROR = 2
EXIT_API_FAILURE = 3
EXIT_INCONCLUSIVE = 4
EXIT_INPUT_ERROR = 3
EXIT_VISUAL_ISSUES = 4
EXIT_AUTH_CONFIG_ERROR = 5

try:
    from english_optimization_bridge import optimize_prompt, is_japanese, TaskType  # noqa: F401
    HAS_ENGLISH_BRIDGE = True
except ImportError:
    HAS_ENGLISH_BRIDGE = False

# WindowsコンソールでのUnicode(絵文字等)エンコードエラー防止
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def get_env_var(key, default=""):
    val = os.environ.get(key, "").strip()
    if not val and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, key)
                val = val.strip()
        except Exception:
            pass
    return val or default

ANTHROPIC_API_KEY = get_env_var("ANTHROPIC_API_KEY")
DEEPSEEK_API_KEY = get_env_var("DEEPSEEK_API_KEY")
GEMINI_API_KEY = get_env_var("GEMINI_API_KEY") or get_env_var("GOOGLE_API_KEY")
OPENAI_API_KEY = get_env_var("OPENAI_API_KEY")
OLLAMA_API_BASE = "http://127.0.0.1:11434"
USD_JPY = float(os.environ.get("USD_JPY_RATE", "155"))

try:
    import litellm
    from litellm import completion as litellm_completion, completion_cost
    litellm.drop_params = True
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False

try:
    from student_frugal_guide import BudgetGovernor
    HAS_FRUGAL_GUIDE = True
except ImportError:
    HAS_FRUGAL_GUIDE = False


def check_frugal_guard(model: str, est_in: int = 500, est_out: int = 500, frugal: bool = False) -> tuple[bool, Any]:
    """
    けち大学生モード（--frugal / AI_TEAM_FRUGAL=1）の予算統制チェック。
    月額100円上限を超過した場合は有料SOTA呼び出しを安全にブロックする。
    """
    is_frugal_active = frugal or (os.environ.get("AI_TEAM_FRUGAL", "").lower() in ("1", "true", "yes"))
    if not is_frugal_active or not HAS_FRUGAL_GUIDE:
        return True, None

    gov = BudgetGovernor()
    can_proceed, reason, est_cost = gov.can_call(model, estimated_input_tokens=est_in, estimated_output_tokens=est_out)
    if not can_proceed:
        print("\n" + "=" * 65, file=sys.stderr)
        print("🎓 [Frugal Governor] 🚫 月額100円上限ガードが作動しました！", file=sys.stderr)
        print("=" * 65, file=sys.stderr)
        for line in reason.splitlines():
            print(f"  {line}", file=sys.stderr)
        print("-" * 65, file=sys.stderr)
        print("💡 推奨アクション: 完全無料枠（Gemini 3.8 Flash / ローカルRTX 4070S Ollama）で実行してください。", file=sys.stderr)
        print("=" * 65 + "\n", file=sys.stderr)
        return False, gov

    return True, gov


def record_frugal_spend(
    gov: Any,
    model: str,
    in_tok: int,
    out_tok: int,
    cache_hit: int = 0,
    cost_jpy: Optional[float] = None,
    cost_usd: Optional[float] = None,
    desc: str = "",
) -> None:
    """実行後の支出を Frugal Governor 台帳に記録する。"""
    if gov is None:
        return
    try:
        gov.record_call(
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_hit_tokens=cache_hit,
            cost_jpy=cost_jpy,
            cost_usd=cost_usd,
            description=desc,
        )
        status = gov.get_status()
        print(f"[Frugal Governor] 💳 当月利用額: {status['total_spent_jpy']:.2f}円 / 残高: {status['remaining_jpy']:.2f}円 (月額上限: {status['budget_jpy']:.1f}円)")
    except Exception as e:
        print(f"[Warning] Frugal Governor 台帳記録エラー: {e}", file=sys.stderr)

# パス除外リスト (ファイルおよびディレクトリ)
DENIED_FILE_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*credentials*.json",
    "*secret*",
    "*service-account*.json",
    "*-key.json",
    ".npmrc",
    ".pypirc",
    "*.lock",
    "package-lock.json",
    "*.min.js",
    "*.map",
]

DENIED_DIR_PATTERNS = [
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    "dist",
    "build",
]

# 秘密情報検出正規表現
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r'"private_key":'),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-proj-"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
]


def is_denied_path(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/").strip("/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return False
    # ディレクトリコンポーネントの検査
    for part in parts[:-1]:
        part_lower = part.lower()
        if part_lower in DENIED_DIR_PATTERNS or part in DENIED_DIR_PATTERNS:
            return True
        for pat in DENIED_FILE_PATTERNS:
            if fnmatch.fnmatch(part_lower, pat.lower()):
                return True
    # ファイル名 (末尾コンポーネント) の検査
    filename = parts[-1]
    filename_lower = filename.lower()
    for pat in DENIED_FILE_PATTERNS:
        if fnmatch.fnmatch(filename_lower, pat.lower()):
            return True
    return False


def contains_secret(text: str) -> bool:
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def extract_diff_filepath(line: str) -> str:
    if not line.startswith("diff --git "):
        return ""
    rest = line[len("diff --git "):].strip()
    if rest.startswith('"a/'):
        end_idx = rest.find('" b/')
        if end_idx != -1:
            return rest[3:end_idx]
    elif rest.startswith("a/"):
        idx = rest.find(" b/")
        if idx != -1:
            return rest[2:idx]
    return rest


def split_diff_blocks(diff_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current_file = ""
    current_lines: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            if current_lines:
                blocks.append((current_file, "\n".join(current_lines)))
                current_lines = []
            current_file = extract_diff_filepath(line)
        current_lines.append(line)

    if current_lines:
        blocks.append((current_file, "\n".join(current_lines)))

    return blocks


def decode_bytes(b: bytes) -> str:
    if not b:
        return ""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return b.decode("cp932")
        except UnicodeDecodeError:
            return b.decode("utf-8", errors="replace")


def run_cmd(cmd, cwd=None):
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return res.returncode, decode_bytes(res.stdout).strip(), decode_bytes(res.stderr).strip()
    except Exception as e:
        return 1, "", str(e)


def format_tool_output(output: str, max_lines: int = 200) -> str:
    lines = output.splitlines()
    if len(lines) > max_lines:
        truncated = "\n".join(lines[:max_lines])
        return f"{truncated}\n... (出力が{max_lines}行を超えたため省略されました。全{len(lines)}行)"
    return output


def probe_python_tool(tool_name: str) -> tuple[bool, str]:
    """
    Pythonツールの利用可否を事前判定する。
    1. shutil.which によるパス検索
    2. sys.executable -m <tool_name> --version によるフォールバック判定
    """
    if shutil.which(tool_name):
        return True, tool_name

    code, _, _ = run_cmd(f'"{sys.executable}" -m {tool_name} --version')
    if code == 0:
        return True, f'"{sys.executable}" -m {tool_name}'

    return False, ""


def probe_node_tool(tool_name: str, cwd: str) -> bool:
    """
    npx --no-install を通じたNodeツールの利用可否を事前判定する。
    """
    if not shutil.which("npx"):
        return False
    code, _, _ = run_cmd(f"npx --no-install {tool_name} --version", cwd=cwd)
    return code == 0


def is_npm_script_runnable(script_content: str, cwd: str) -> bool:
    """
    npm スクリプトが実行可能か事前判定する。
    """
    if not shutil.which("npm"):
        return False
    if os.path.isdir(os.path.join(cwd, "node_modules")):
        return True
    first_cmd = script_content.strip().split()[0] if script_content.strip() else ""
    if first_cmd and shutil.which(first_cmd):
        return True
    return False


def is_python_project(root_dir: str) -> bool:
    if os.path.isfile(os.path.join(root_dir, "pyproject.toml")) or os.path.isfile(os.path.join(root_dir, "setup.cfg")):
        return True
    ignored_dirs = {"node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in ignored_dirs and not d.startswith(".")]
        for f in filenames:
            if f.endswith(".py"):
                return True
    return False


def is_node_project(root_dir: str) -> bool:
    return os.path.isfile(os.path.join(root_dir, "package.json"))


def is_pytest_cov_available() -> bool:
    try:
        import pytest_cov  # type: ignore # noqa: F401
        return True
    except ImportError:
        code, _, _ = run_cmd(f'"{sys.executable}" -c "import pytest_cov"')
        return code == 0


def handle_review(repo_path=None, model="claude-opus-5-5", deep=False, thinking_budget=2048, dry_run=False, frugal=False):
    cwd = repo_path or os.getcwd()
    mode_label = f"深層思考モード (Extended Thinking {thinking_budget} tokens)" if deep else "通常モード"
    print(f"[Review] リポジトリ差分を抽出中 ({cwd}) [{mode_label}]...", file=sys.stderr)

    # 1. git diff 取得 (staged + unstaged 全体を対象)
    code, diff_out, _ = run_cmd("git diff HEAD", cwd=cwd)
    if code != 0:
        _, unstaged, _ = run_cmd("git diff", cwd=cwd)
        _, staged, _ = run_cmd("git diff --cached", cwd=cwd)
        diff_out = (unstaged + "\n" + staged).strip() if (unstaged or staged) else ""

    tracked_blocks = split_diff_blocks(diff_out)

    # 2. 未追跡ファイル (untracked files) の抽出と差分ブロック合成
    code_ls, untracked_out, _ = run_cmd("git ls-files --others --exclude-standard", cwd=cwd)
    untracked_blocks: list[tuple[str, str]] = []

    if code_ls == 0 and untracked_out:
        for rel_file in untracked_out.splitlines():
            rel_file = rel_file.strip()
            if not rel_file:
                continue
            if is_denied_path(rel_file):
                continue
            full_path = os.path.join(cwd, rel_file)
            if not os.path.isfile(full_path):
                continue
            try:
                if os.path.getsize(full_path) > 200 * 1024:
                    continue
                with open(full_path, "r", encoding="utf-8", errors="strict") as f:
                    content = f.read()
            except (UnicodeDecodeError, OSError):
                # バイナリまたはデコード不能なファイル、アクセス不可ファイルはスキップ
                continue

            norm_path = rel_file.replace("\\", "/")
            lines = content.splitlines()
            block_lines = [
                f"diff --git a/{norm_path} b/{norm_path}",
                "new file",
                "--- /dev/null",
                f"+++ b/{norm_path}",
            ]
            for line_content in lines:
                block_lines.append(f"+{line_content}")
            untracked_blocks.append((norm_path, "\n".join(block_lines)))

    # 3. 差分ブロックのフィルタリング (パス除外 & 秘密情報スキャン)
    all_blocks = tracked_blocks + untracked_blocks
    accepted_blocks: list[str] = []

    for filepath, block_content in all_blocks:
        if is_denied_path(filepath):
            continue
        if contains_secret(block_content):
            print(f"[Security Warning] 秘密情報パターンを検出したため差分ブロックを除外しました: {filepath}", file=sys.stderr)
            continue
        accepted_blocks.append(block_content)

    if not accepted_blocks:
        print("[Review] 変更差分（git diff）がありません。コミット前の変更がある状態で実行してください。", file=sys.stderr)
        return

    # 全差分テキストの統合
    combined_diff = "\n".join(accepted_blocks)
    diff_lines = combined_diff.splitlines()

    # トークン節約: 最大1000行にクリップ
    if len(diff_lines) > 1000:
        dropped = len(diff_lines) - 1000
        print(f"[Warning] 差分が1000行を超えたため、末尾{dropped}行を省略しました。", file=sys.stderr)
        diff_out = "\n".join(diff_lines[:1000]) + f"\n... (以降の差分 {dropped} 行は省略)"
    else:
        diff_out = combined_diff

    system_instruction = """You are a strict, adversarial software architect and red-team reviewer.
Audit the provided Git diff. Do NOT praise or greet.
Identify ONLY concrete risks causing production outages, security vulnerabilities, or architectural decay in concise bullet points:
1. Boundary values, unhandled exceptions, and fail-open security flaws.
2. Breaking changes or side effects on existing interfaces and type contracts.
3. Performance regressions, lock contention, or unnecessary complexity.
4. Actionable refactoring recommendations with minimal code snippets.

Output your final critique in clear, professional Japanese for the engineering team."""

    # モデル名のマッピング (OpenAI の API モデルIDは gpt-6.1-sol)
    if model.startswith("gpt"):
        api_model = "gpt-6.1-sol" if model in ["gpt-sol", "gpt-6-1-sol"] else model
    else:
        api_model = model

    # --dry-run の場合はAPI呼び出しを行わずに diff パッケージと対象モデルを表示して終了
    if dry_run:
        target_display = f"{model} (API: {api_model})" if api_model != model else model
        print(f"[Dry-Run] 対象モデル: {target_display}", file=sys.stderr)
        print(f"=== 差分パッケージ ({model}) ===\n")
        print(system_instruction + f"\n\n【Git Diff】\n```diff\n{diff_out}\n```")
        return

    can_proceed, gov = check_frugal_guard(model, est_in=1000, est_out=1000, frugal=frugal)
    if not can_proceed:
        return

    # Gemini 4 Argon による差分レビュー
    if model.startswith("gemini"):
        if not GEMINI_API_KEY:
            print(f"[Notice] GEMINI_API_KEY が未設定です。設定すると {model} が自動レビューします。", file=sys.stderr)
            print("以下のプロンプトが生成されました（差分パッケージ）:\n", file=sys.stderr)
            print(system_instruction + f"\n\n【Git Diff】\n```diff\n{diff_out}\n```")
            return

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {"role": "user", "parts": [{"text": f"【Git Diff】\n```diff\n{diff_out}\n```"}]}
            ],
            "generationConfig": {
                "maxOutputTokens": 4096,
                "temperature": 0.2
            }
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            print(f"[Review] {model} に差分レビューを送信中...", file=sys.stderr)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts)
                    print(f"\n=== {model} 敵対的差分レビュー ===")
                    print(text)

                meta = data.get("usageMetadata", {})
                in_tok = meta.get("promptTokenCount", 0)
                out_tok = meta.get("candidatesTokenCount", 0)
                # Gemini 4 Argon / Carbon: $2.00 / $10.00 per 1M
                cost_usd = (in_tok * 2.0 + out_tok * 10.0) / 1_000_000
                cost_jpy = cost_usd * USD_JPY
                print(f"\n[コスト情報] モデル: {model} | 入力: {in_tok} | 出力: {out_tok} | 概算費用: 約{cost_jpy:.2f}円 (${cost_usd:.4f})")
                record_frugal_spend(gov, model, in_tok, out_tok, cost_jpy=cost_jpy, cost_usd=cost_usd, desc="Gemini Review Diff Audit")
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            print(f"[Error] Gemini APIエラー ({e.code}): {err_msg}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except urllib.error.URLError as e:
            print(f"[Error] Gemini ネットワーク接続エラー: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except TimeoutError as e:
            print(f"[Error] Gemini APIタイムアウト: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except Exception as e:
            print(f"[Error] Gemini API呼び出し失敗: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        return

    # GPT-6.1 Sol による差分レビュー
    if model.startswith("gpt"):
        if not OPENAI_API_KEY:
            print(f"[Notice] OPENAI_API_KEY が未設定です。設定すると {api_model} が自動レビューします。", file=sys.stderr)
            print("以下のプロンプトが生成されました（差分パッケージ）:\n", file=sys.stderr)
            print(system_instruction + f"\n\n【Git Diff】\n```diff\n{diff_out}\n```")
            return

        # LiteLLM 統合パス（自動コスト計算）
        if HAS_LITELLM and OPENAI_API_KEY:
            try:
                os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
                print(f"[Review] {api_model} に LiteLLM で差分レビューを送信中 ({mode_label})...", file=sys.stderr)
                litellm_model = f"openai/{api_model}" if not api_model.startswith("openai/") else api_model
                resp = litellm_completion(
                    model=litellm_model,
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": f"【Git Diff】\n```diff\n{diff_out}\n```"}
                    ],
                    max_tokens=8192
                )
                msg = resp.choices[0].message
                review_text = msg.content or ""
                print(f"\n=== {api_model} 敵対的差分レビュー ===")
                print(review_text)

                cost_usd = completion_cost(completion_response=resp)
                cost_jpy = cost_usd * USD_JPY
                u = resp.usage
                print(f"\n[コスト情報] モデル: {api_model} | 入力: {u.prompt_tokens} | 出力: {u.completion_tokens} | 費用(自動計算): 約{cost_jpy:.2f}円 (${cost_usd:.5f})")
                return
            except Exception as e:
                print(f"[Warning] LiteLLM 呼び出しフォールバック: {e}", file=sys.stderr)

        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": api_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"【Git Diff】\n```diff\n{diff_out}\n```"}
            ],
            "max_tokens": 8192
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            print(f"[Review] {api_model} に差分レビューを送信中...", file=sys.stderr)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data["choices"][0]["message"]
                print(f"\n=== {api_model} 敵対的差分レビュー ===")
                print(choice.get("content", ""))

                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                # GPT-6.1 Sol: $2.00 / $10.00 per 1M
                cost_usd = (in_tok * 2.0 + out_tok * 10.0) / 1_000_000
                cost_jpy = cost_usd * USD_JPY
                print(f"\n[コスト情報] モデル: {api_model} | 入力: {in_tok} | 出力: {out_tok} | 概算費用: 約{cost_jpy:.2f}円 (${cost_usd:.4f})")
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            print(f"[Error] OpenAI APIエラー ({e.code}): {err_msg}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except urllib.error.URLError as e:
            print(f"[Error] OpenAI ネットワーク接続エラー: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except TimeoutError as e:
            print(f"[Error] OpenAI APIタイムアウト: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except Exception as e:
            print(f"[Error] OpenAI API呼び出し失敗: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        return

    # Claude による差分レビュー
    if not ANTHROPIC_API_KEY:
        print(f"[Notice] ANTHROPIC_API_KEY が未設定です。設定すると {model} が自動レビューします。", file=sys.stderr)
        print("以下のプロンプトが生成されました（差分パッケージ）:\n", file=sys.stderr)
        print(system_instruction + f"\n\n【Git Diff】\n```diff\n{diff_out}\n```")
        return

    # LiteLLM 統合パス（自動コスト計算・思考対応）
    if HAS_LITELLM and ANTHROPIC_API_KEY:
        try:
            os.environ["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
            print(f"[Review] {model} に LiteLLM で差分レビューを送信中 ({mode_label})...", file=sys.stderr)
            litellm_model = f"anthropic/{model}" if not model.startswith("anthropic/") else model
            resp = litellm_completion(
                model=litellm_model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"【Git Diff】\n```diff\n{diff_out}\n```"}
                ],
                max_tokens=16384 if deep else 8192
            )
            msg = resp.choices[0].message
            review_text = msg.content or ""
            fields = getattr(msg, "provider_specific_fields", {}) or {}
            thinking = fields.get("thinking_blocks", [])

            if thinking:
                print(f"\n--- Claude 思考プロセス (<thinking>) ---\n{''.join(t.get('thinking', '') for t in thinking)}\n")

            print(f"\n=== {model} 敵対的差分レビュー ===")
            print(review_text)

            cost_usd = completion_cost(completion_response=resp)
            cost_jpy = cost_usd * USD_JPY
            u = resp.usage
            print(f"\n[コスト情報] モデル: {model} | 入力: {u.prompt_tokens} | 出力: {u.completion_tokens} | 費用(自動計算): 約{cost_jpy:.2f}円 (${cost_usd:.5f})")
            return
        except Exception as e:
            print(f"[Warning] LiteLLM 呼び出しフォールバック: {e}", file=sys.stderr)

    # Anthropic API呼び出し (プロンプトキャッシュ対応・フォールバック)
    url = "https://api.anthropic.com/v1/messages"
    max_tokens = 16384 if deep else 8192
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system_instruction,
                "cache_control": {"type": "ephemeral"}
            }
        ],
        "messages": [
            {"role": "user", "content": f"【Git Diff】\n```diff\n{diff_out}\n```"}
        ]
    }

    if deep:
        if "opus" in model.lower():
            payload["thinking"] = {"type": "adaptive"}
        else:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": max(1024, thinking_budget)
            }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json"
        },
        method="POST"
    )

    try:
        print(f"[Review] {model} に差分レビューを送信中 ({mode_label})...", file=sys.stderr)
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("content", [])
            thinking_blocks = [c.get("thinking", "") for c in content if c.get("type") == "thinking"]
            text_blocks = [c.get("text", "") for c in content if c.get("type") == "text"]

            if thinking_blocks:
                print(f"\n--- Claude 思考プロセス (<thinking>) ---\n{''.join(thinking_blocks)}\n")

            print(f"\n=== {model} 敵対的差分レビュー ===")
            print("".join(text_blocks))

            # トークン消費・実費用の透明化
            usage = data.get("usage", {})
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            c_read = usage.get("cache_read_input_tokens", 0)
            c_create = usage.get("cache_creation_input_tokens", 0)

            # Opus 5.5: In $4/M, Out $20/M | Sonnet 5.5: In $2/M, Out $10/M
            rate_in = 4.0 if "opus" in model.lower() else 2.0
            rate_out = 20.0 if "opus" in model.lower() else 10.0
            cost_usd = (in_tok * rate_in + out_tok * rate_out + c_create * (rate_in * 1.25) + c_read * (rate_in * 0.1)) / 1_000_000
            cost_jpy = cost_usd * USD_JPY
            print(f"\n[コスト情報] モード: {mode_label} | 入力: {in_tok} (キャッシュ作成: {c_create}, 読込: {c_read}) | 出力/思考: {out_tok} | 概算費用: 約{cost_jpy:.2f}円 (${cost_usd:.4f})")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        print(f"[Error] Anthropic APIエラー ({e.code}): {err_msg}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except urllib.error.URLError as e:
        print(f"[Error] Anthropic ネットワーク接続エラー: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except TimeoutError as e:
        print(f"[Error] Anthropic APIタイムアウト: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except Exception as e:
        print(f"[Error] Anthropic API呼び出し失敗: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)


def handle_logic(problem_desc, model="deepseek-flash", frugal=False):
    # モデルエイリアス正規化 & 単価設定
    # deepseek-v4-pro / deepseek-pro: 1.6T MoE ($0.43 / $0.87 per 1M)
    # deepseek-flash: 552B MoE ($0.30 / $1.20 per 1M)
    if "pro" in model.lower():
        api_model = "deepseek-v4-pro"
        p_in, p_out = 0.43, 0.87
    else:
        api_model = "deepseek-flash"
        p_in, p_out = 0.30, 1.20

    print(f"[Logic] 難関ロジックの解析を開始 ({api_model})...", file=sys.stderr)
    can_proceed, gov = check_frugal_guard(api_model, est_in=1000, est_out=2000, frugal=frugal)
    if not can_proceed:
        return

    if DEEPSEEK_API_KEY:
        # DeepSeek API (deepseek-v4-pro / deepseek-flash)
        url = "https://api.deepseek.com/chat/completions"
        payload = {
            "model": api_model,
            "max_tokens": 32768,
            "messages": [
                {"role": "system", "content": "You are an expert in advanced algorithms, mathematical logic, and concurrent systems. Break down the problem step-by-step and provide mathematically sound, deadlock-free solutions. Output the final explanation and code in clear Japanese."},
                {"role": "user", "content": problem_desc}
            ]
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data["choices"][0]["message"]
                finish_reason = data["choices"][0].get("finish_reason", "")
                reasoning = choice.get("reasoning_content", "")
                content = choice.get("content", "")

                if reasoning:
                    print(f"--- 思考プロセス (<think>) ---\n{reasoning}\n")

                if finish_reason == "length" or not content.strip():
                    print(f"[Warning] DeepSeek の出力トークン上限（max_tokens=32768）に到達したか、content が空です (finish_reason='{finish_reason}')。", file=sys.stderr)

                if content.strip():
                    print(f"--- 最終回答 ---\n{content}")

                # コスト情報の透明化
                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                c_hit = usage.get("prompt_cache_hit_tokens", 0)
                # Pricing: V4-Pro ($0.43/M in, $0.87/M out) vs Flash ($0.30/M in, $1.20/M out)
                cost_usd = ((in_tok - c_hit) * p_in + c_hit * (p_in * 0.25) + out_tok * p_out) / 1_000_000
                cost_jpy = cost_usd * USD_JPY
                print(f"\n[コスト情報] モデル: {api_model} | 入力: {in_tok} (キャッシュヒット: {c_hit}) | 出力: {out_tok} | 概算費用: 約{cost_jpy:.2f}円 (${cost_usd:.4f})")
                record_frugal_spend(gov, api_model, in_tok, out_tok, cache_hit=c_hit, cost_jpy=cost_jpy, cost_usd=cost_usd, desc="Logic problem solving")
                return
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            print(f"[Warning] DeepSeek API接続失敗 ({e.code}: {err_msg})。ローカルR1にフォールバックします...", file=sys.stderr)
        except urllib.error.URLError as e:
            print(f"[Warning] DeepSeek ネットワーク接続エラー ({e})。ローカルR1にフォールバックします...", file=sys.stderr)
        except TimeoutError as e:
            print(f"[Warning] DeepSeek APIタイムアウト ({e})。ローカルR1にフォールバックします...", file=sys.stderr)
        except Exception as e:
            print(f"[Warning] DeepSeek APIエラー ({e})。ローカルR1にフォールバックします...", file=sys.stderr)

    # フォールバック: 自宅ローカルGPUの local-reasoner (DeepSeek R1 14B)
    print("[Logic] ローカルGPU (local-reasoner: DeepSeek R1 14B) で無料推論中...", file=sys.stderr)
    url = f"{OLLAMA_API_BASE}/api/generate"
    payload = {
        "model": "local-reasoner",
        "prompt": problem_desc,
        "stream": True
    }
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                if line:
                    c = json.loads(line.decode("utf-8"))
                    sys.stdout.write(c.get("response", ""))
                    sys.stdout.flush()
            print("")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        print(f"[Error] ローカルLLM APIエラー ({e.code}): {err_msg}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except urllib.error.URLError as e:
        print(f"[Error] ローカルLLM ネットワーク接続エラー: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except TimeoutError as e:
        print(f"[Error] ローカルLLM タイムアウト: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except Exception as e:
        print(f"[Error] ローカルLLM呼び出し失敗: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)


def handle_architect(problem_desc, model="deepseek-v4-pro", frugal=False):
    is_gemini_model = model.startswith("gemini")
    can_proceed, gov = check_frugal_guard(model, est_in=1500, est_out=2500, frugal=frugal)
    if not can_proceed:
        return

    system_instruction = """You are a Principal Software Architect and Chief Technical Strategist acting as the Commander of an autonomous AI engineering team.
Your mission:
1. High-level Architecture & Interface/Type Contracts:
   Define clean, robust architectural boundaries, data flow models, and TypeScript/Python interface type contracts. Eliminate circular dependencies and race conditions.
2. Atomic Task Decomposition for Parallel Subagents (Model='flash'):
   Decompose the implementation into independent, parallelizable work units. For EACH subagent task, specify:
   - Role / Job Title (e.g. 'Database Schema Specialist', 'API Handler Engineer')
   - Target Files & Directories
   - Step-by-Step Implementation Instructions
   - Concrete, deterministic Done Criteria (static typing, unit tests, linter rules)
3. Edge Cases, Security/Concurrency Risks, and Verification Plan:
   Highlight potential deadlock, memory leaks, boundary value failures, and fail-open security flaws. Prescribe precise automated verification commands (tsc, ruff, pytest).

Output your architectural specifications and subagent dispatch instructions in clear, highly structured, professional Japanese."""

    # Gemini 4 Argon / Carbon によるチーフアーキテクト設計 (明示的指定時のみ)
    if is_gemini_model:
        target_model = model
        print(f"[Architect] チーフアーキテクト設計・タスク分解解析を開始 ({target_model})...", file=sys.stderr)
        if not GEMINI_API_KEY:
            print(f"[Notice] GEMINI_API_KEY が未設定です。設定すると {target_model} が自動実行します。", file=sys.stderr)
            print("以下のプロンプトが生成されました:\n", file=sys.stderr)
            print(system_instruction + f"\n\n【課題・要件】\n{problem_desc}")
            return

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {"role": "user", "parts": [{"text": problem_desc}]}
            ],
            "generationConfig": {
                "maxOutputTokens": 4096,
                "temperature": 0.2
            }
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts)
                    print(f"\n=== {target_model} チーフアーキテクト設計・タスク分解結果 ===")
                    print(text)

                meta = data.get("usageMetadata", {})
                in_tok = meta.get("promptTokenCount", 0)
                out_tok = meta.get("candidatesTokenCount", 0)
                cost_usd = (in_tok * 2.0 + out_tok * 10.0) / 1_000_000
                cost_jpy = cost_usd * USD_JPY
                print(f"\n[コスト情報] モデル: {target_model} | 入力: {in_tok} | 出力: {out_tok} | 概算費用: 約{cost_jpy:.2f}円 (${cost_usd:.4f})")
                record_frugal_spend(gov, target_model, in_tok, out_tok, cost_jpy=cost_jpy, cost_usd=cost_usd, desc="Architect design (Gemini)")
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            print(f"[Error] Gemini APIエラー ({e.code}): {err_msg}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except urllib.error.URLError as e:
            print(f"[Error] Gemini ネットワーク接続エラー: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except TimeoutError as e:
            print(f"[Error] Gemini APIタイムアウト: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except Exception as e:
            print(f"[Error] Gemini API呼び出し失敗: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        return

    # DeepSeek V4 Pro による仮想 Commander 設計・タスク分解 (デフォルト)
    target_model = "deepseek-v4-pro"
    print(f"[Architect] 仮想Commander設計・タスク分解解析を開始 ({target_model})...", file=sys.stderr)

    if DEEPSEEK_API_KEY:
        url = "https://api.deepseek.com/chat/completions"
        payload = {
            "model": target_model,
            "max_tokens": 32768,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": problem_desc}
            ]
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data["choices"][0]["message"]
                finish_reason = data["choices"][0].get("finish_reason", "")
                reasoning = choice.get("reasoning_content", "")
                content = choice.get("content", "")

                if reasoning:
                    print(f"--- 思考プロセス (<think>) ---\n{reasoning}\n")

                if finish_reason == "length" or not content.strip():
                    print(f"[Warning] DeepSeek の出力トークン上限（max_tokens=32768）に到達したか、content が空です (finish_reason='{finish_reason}')。", file=sys.stderr)

                print(f"\n=== {target_model} 仮想Commander 設計・タスク分解結果 ===")
                print(content)

                # コスト情報の透明化 ($0.43/M in, $0.87/M out, キャッシュヒット 25%)
                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                c_hit = usage.get("prompt_cache_hit_tokens", 0)
                cost_usd = ((in_tok - c_hit) * 0.43 + c_hit * (0.43 * 0.25) + out_tok * 0.87) / 1_000_000
                cost_jpy = cost_usd * USD_JPY
                print(f"\n[コスト情報] モデル: {target_model} | 入力: {in_tok} (キャッシュヒット: {c_hit}) | 出力: {out_tok} | 概算費用: 約{cost_jpy:.2f}円 (${cost_usd:.4f})")
                record_frugal_spend(gov, target_model, in_tok, out_tok, cache_hit=c_hit, cost_jpy=cost_jpy, cost_usd=cost_usd, desc="Architect design (DeepSeek)")
                return
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            print(f"[Error] DeepSeek APIエラー ({e.code}): {err_msg}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except urllib.error.URLError as e:
            print(f"[Error] DeepSeek ネットワーク接続エラー: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except TimeoutError as e:
            print(f"[Error] DeepSeek APIタイムアウト: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)
        except Exception as e:
            print(f"[Error] DeepSeek API呼び出し失敗: {e}", file=sys.stderr)
            sys.exit(EXIT_API_FAILURE)

    # DEEPSEEK_API_KEY 未設定時はローカル GPU (local-reasoner) にフォールバック
    print("[Architect] DEEPSEEK_API_KEY 未設定のため、ローカルGPU (local-reasoner: DeepSeek R1 14B) で設計・タスク分解推論中...", file=sys.stderr)
    url = f"{OLLAMA_API_BASE}/api/generate"
    payload = {
        "model": "local-reasoner",
        "prompt": f"{system_instruction}\n\n【課題・要件】\n{problem_desc}",
        "stream": True
    }
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            print("\n=== local-reasoner (ローカルGPU) 仮想Commander 設計結果 ===")
            for line in resp:
                if line:
                    c = json.loads(line.decode("utf-8"))
                    sys.stdout.write(c.get("response", ""))
                    sys.stdout.flush()
            print("")
            return
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        print(f"[Error] ローカルLLM APIエラー ({e.code}): {err_msg}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except urllib.error.URLError as e:
        print(f"[Error] ローカルLLM ネットワーク接続エラー: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except TimeoutError as e:
        print(f"[Error] ローカルLLM タイムアウト: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)
    except Exception as e:
        print(f"[Error] ローカルLLM呼び出し失敗: {e}", file=sys.stderr)
        sys.exit(EXIT_API_FAILURE)


def handle_check(repo_path=None, build=False) -> int:
    cwd = repo_path or os.getcwd()
    print(f"=== 機械的ゲートキーパー実行 ({cwd}) ===", file=sys.stderr)

    is_py = is_python_project(cwd)
    is_node = is_node_project(cwd)

    if not is_py and not is_node:
        print(f"[Notice] 対象プロジェクト（Python または Node/TS）が検出されませんでした: {cwd}", file=sys.stderr)
        return EXIT_PASS

    executed_checks = 0
    failed_steps: list[str] = []

    # Python プロジェクトの検査
    if is_py:
        print("\n--- Python 品質チェック ---", file=sys.stderr)

        # 1. 型チェック (mypy)
        print("[Check] 静的型チェック (mypy)...", file=sys.stderr)
        mypy_avail, mypy_cmd = probe_python_tool("mypy")
        if not mypy_avail:
            print("  [SKIP] mypy: 未インストール (not installed)")
        else:
            code, out, err = run_cmd(f"{mypy_cmd} . --ignore-missing-imports", cwd=cwd)
            combined = (out + "\n" + err).strip() if err else out
            executed_checks += 1
            if code == 0:
                print("  [PASS] mypy: 型エラーなし")
            else:
                print(f"  [FAIL] mypy:\n{format_tool_output(combined)}")
                failed_steps.append("mypy")

        # 2. Linter (ruff)
        print("[Check] Linter (ruff)...", file=sys.stderr)
        ruff_avail, ruff_cmd = probe_python_tool("ruff")
        if not ruff_avail:
            print("  [SKIP] ruff: 未インストール (not installed)")
        else:
            code, out, err = run_cmd(f"{ruff_cmd} check .", cwd=cwd)
            combined = (out + "\n" + err).strip() if err else out
            executed_checks += 1
            if code == 0:
                print("  [PASS] ruff: 構文ルール違反なし")
            else:
                print(f"  [FAIL] ruff:\n{format_tool_output(combined)}")
                failed_steps.append("ruff")

        # 3. 単体テスト (pytest)
        print("[Check] 単体テスト & カバレッジ (pytest)...", file=sys.stderr)
        pytest_avail, pytest_cmd = probe_python_tool("pytest")
        if not pytest_avail:
            print("  [SKIP] pytest: 未インストール (not installed)")
        else:
            cov_flag = " --cov=. --cov-report=term-missing" if is_pytest_cov_available() else ""
            code, out, err = run_cmd(f"{pytest_cmd}{cov_flag}", cwd=cwd)
            combined = (out + "\n" + err).strip() if err else out
            if code == 0:
                executed_checks += 1
                print("  [PASS] pytest: テスト全通過")
            elif code == 5:
                print("  [SKIP] pytest: テスト未検出 (no tests collected)")
            else:
                executed_checks += 1
                print(f"  [FAIL] pytest:\n{format_tool_output(combined)}")
                failed_steps.append("pytest")

    # Node/TS プロジェクトの検査
    if is_node:
        print("\n--- Node/TS 品質チェック ---", file=sys.stderr)
        pkg_path = os.path.join(cwd, "package.json")
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
        except Exception as e:
            print(f"  [FAIL] package.json 読み込み失敗: {e}")
            failed_steps.append("package.json")
            pkg_data = {}

        scripts = pkg_data.get("scripts", {})
        deps = pkg_data.get("dependencies", {})
        dev_deps = pkg_data.get("devDependencies", {})

        # 1. TypeScript 型チェック (tsconfig.json がある場合)
        if os.path.isfile(os.path.join(cwd, "tsconfig.json")):
            print("[Check] 静的型チェック (tsc)...", file=sys.stderr)
            if not probe_node_tool("tsc", cwd):
                print("  [SKIP] tsc: 未インストール (not installed)")
            else:
                code, out, err = run_cmd("npx --no-install tsc --noEmit", cwd=cwd)
                combined = (out + "\n" + err).strip() if err else out
                executed_checks += 1
                if code == 0:
                    print("  [PASS] tsc: 型エラーなし")
                else:
                    print(f"  [FAIL] tsc:\n{format_tool_output(combined)}")
                    failed_steps.append("tsc")

        # 2. ESLint (eslint設定ファイル または package.json deps に eslint がある場合)
        try:
            cwd_files = os.listdir(cwd)
        except Exception:
            cwd_files = []
        has_eslint_config = any(
            f.startswith("eslint.config.") or f.startswith(".eslintrc")
            for f in cwd_files
        )
        has_eslint_dep = ("eslint" in deps) or ("eslint" in dev_deps)

        if has_eslint_config or has_eslint_dep:
            print("[Check] Linter (eslint)...", file=sys.stderr)
            if not probe_node_tool("eslint", cwd):
                print("  [SKIP] eslint: 未インストール (not installed)")
            else:
                code, out, err = run_cmd("npx --no-install eslint .", cwd=cwd)
                combined = (out + "\n" + err).strip() if err else out
                executed_checks += 1
                if code == 0:
                    print("  [PASS] eslint: 構文ルール違反なし")
                else:
                    print(f"  [FAIL] eslint:\n{format_tool_output(combined)}")
                    failed_steps.append("eslint")

        # 3. npm test (scripts に test があり、デフォルトメッセージでない場合)
        test_script = scripts.get("test", "").strip()
        is_default_test = 'echo "Error: no test specified" && exit 1' in test_script or "no test specified" in test_script
        if "test" in scripts and not is_default_test:
            print("[Check] 単体テスト (npm test)...", file=sys.stderr)
            if not is_npm_script_runnable(test_script, cwd):
                print("  [SKIP] npm test: 未インストール (not installed)")
            else:
                code, out, err = run_cmd("npm test --silent", cwd=cwd)
                combined = (out + "\n" + err).strip() if err else out
                executed_checks += 1
                if code == 0:
                    print("  [PASS] npm test: テスト全通過")
                else:
                    print(f"  [FAIL] npm test:\n{format_tool_output(combined)}")
                    failed_steps.append("npm test")

        # 4. npm run build (オプショナル --build 指定時)
        if build:
            if "build" in scripts:
                build_script = scripts.get("build", "").strip()
                print("[Check] ビルド検証 (npm run build)...", file=sys.stderr)
                if not is_npm_script_runnable(build_script, cwd):
                    print("  [SKIP] npm run build: 未インストール (not installed)")
                else:
                    code, out, err = run_cmd("npm run build", cwd=cwd)
                    combined = (out + "\n" + err).strip() if err else out
                    executed_checks += 1
                    if code == 0:
                        print("  [PASS] npm run build: ビルド成功")
                    else:
                        print(f"  [FAIL] npm run build:\n{format_tool_output(combined)}")
                        failed_steps.append("npm run build")

    # 最終ゲート判定サマリー
    if failed_steps:
        print(f"\n[GATE] FAIL ({', '.join(failed_steps)})")
        return EXIT_GATE_FAIL
    elif executed_checks == 0:
        print("\n[GATE] INCONCLUSIVE (no checks executed)")
        return EXIT_INCONCLUSIVE
    else:
        print("\n[GATE] PASS")
        return EXIT_PASS



def handle_vision(image_path, compare_path=None, prompt=None, format_type="markdown", output_file=None, model="gemini-2.5-flash", fail_on_issues=True):
    try:
        from vision_client import GeminiFlashVisionClient, VisionInputError, VisionAuthError, VisionApiError
        from vision_report import print_report
    except ImportError as e:
        print(f"[Vision Error] Required modules not found: {e}", file=sys.stderr)
        return EXIT_GATE_FAIL

    client = GeminiFlashVisionClient(model=model)
    try:
        result = client.audit_image(
            image_path=image_path,
            compare_image_path=compare_path,
            custom_prompt=prompt,
        )
    except VisionInputError as e:
        print(f"[Vision Input Error] {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except VisionAuthError as e:
        print(f"[Vision Auth Error] {e}", file=sys.stderr)
        return EXIT_AUTH_CONFIG_ERROR
    except VisionApiError as e:
        print(f"[Vision API Error] {e}", file=sys.stderr)
        return EXIT_API_FAILURE
    except Exception as e:
        print(f"[Vision Error] Unexpected failure: {e}", file=sys.stderr)
        return EXIT_GATE_FAIL

    print_report(result, format_type=format_type, output_file=output_file)

    if fail_on_issues and result.get("status") == "issues_found":
        return EXIT_VISUAL_ISSUES
    return EXIT_PASS


def handle_fast_cu(action_cmd, args_dict):
    try:
        from fast_cu_client import FastCUClient
    except ImportError as e:
        print(f"[Fast CU Error] Client module not found: {e}", file=sys.stderr)
        return EXIT_GATE_FAIL

    client = FastCUClient()
    try:
        if action_cmd == "ping":
            print(json.dumps(client.ping(), indent=2))
        elif action_cmd == "screenshot":
            print(json.dumps(client.capture_screen(), indent=2))
        elif action_cmd == "focus":
            print(json.dumps(client.execute_action({"type": "focus_window", "title": args_dict.get("title", "")}), indent=2))
        elif action_cmd == "batch":
            file_path = args_dict.get("file")
            if file_path:
                with open(file_path, "r", encoding="utf-8") as f:
                    acts = json.load(f)
            else:
                acts = json.loads(args_dict.get("json", "[]"))
            print(json.dumps(client.execute_batch(acts), indent=2))
        return EXIT_PASS
    except Exception as e:
        print(f"[Fast CU Error] Failed to execute {action_cmd}: {e}", file=sys.stderr)
        return EXIT_GATE_FAIL


def handle_computer_use(goal, max_steps=10, dry_run=False, model="auto", image=None):
    try:
        from gemini_computer_use import GeminiComputerUse
    except ImportError as e:
        print(f"[Computer Use Error] Required modules not found: {e}", file=sys.stderr)
        return EXIT_GATE_FAIL

    agent = GeminiComputerUse(model=model, dry_run=dry_run, max_steps=max_steps, static_image=image)
    success = agent.run(goal)
    return EXIT_PASS if success else EXIT_GATE_FAIL


def main():
    parser = argparse.ArgumentParser(description="AI Team Gatekeeper (5-Tier Synergy)")
    subparsers = parser.add_subparsers(dest="command")

    p_rev = subparsers.add_parser("review", help="Git差分を敵対的レビューさせる（Claude, Gemini 4 Argon, または GPT-6.1 Sol）")
    p_rev.add_argument("--path", default=None, help="リポジトリのパス")
    p_rev.add_argument(
        "--model",
        default="claude-opus-5-5",
        choices=[
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "gemini-4-argon",
            "gemini-4-carbon",
            "gpt-6-1-sol",
            "gpt-sol",
        ],
        help="使用するモデル（デフォルト: claude-opus-5-5 [$4/$20], 選択肢: claude-opus-5-5, claude-sonnet-5-5 [$2/$10], gemini-4-argon [$2/$10], gpt-6-1-sol / gpt-sol [$2/$10]）"
    )
    p_rev.add_argument("--deep", action="store_true", help="Claudeの拡張思考（Extended Thinking: デフォルト2048トークン）を有効化して深層監査する")
    p_rev.add_argument("--thinking-budget", type=int, default=2048, help="深層思考時の思考トークン予算（デフォルト: 2048、最小1024）")
    p_rev.add_argument("--dry-run", action="store_true", help="API呼び出しを行わず、差分パッケージと送信対象モデルを表示して終了する")
    p_rev.add_argument("--frugal", action="store_true", help="けち大学生モード：月額100円上限ガバナーを有効化し、予算超過時の有料SOTA呼び出しをブロック")


    p_arch = subparsers.add_parser("architect", help="初期設計・仕様策定や、Flashスタック時の高難度エスカレーション（DeepSeek V4 Pro 仮想Commander / Gemini 4 Argon）")
    p_arch.add_argument("problem", nargs="?", default="", help="要件定義、アーキテクチャ設計課題、またはスタックした問題のプロンプト（--file と併用時はその前に付与）")
    p_arch.add_argument("--file", "-f", default=None, help="プロンプトを読み込むUTF-8テキストファイルのパス")
    p_arch.add_argument(
        "--model",
        default="deepseek-v4-pro",
        choices=["deepseek-v4-pro", "deepseek-pro", "gemini-4-argon", "gemini-4-carbon"],
        help="使用するモデル（デフォルト: deepseek-v4-pro [$0.43/$0.87], 選択肢: deepseek-v4-pro / deepseek-pro [$0.43/$0.87], gemini-4-argon [$2/$10], gemini-4-carbon [$2/$10]）"
    )

    p_log = subparsers.add_parser("logic", help="難関ロジック・アルゴリズムをDeepSeekに解かせる")
    p_log.add_argument("problem", nargs="?", default="", help="問題のプロンプト（--file と併用時はその前に付与）")
    p_log.add_argument("--file", "-f", default=None, help="プロンプトを読み込むUTF-8テキストファイルのパス")
    p_log.add_argument(
        "--model",
        default="deepseek-flash",
        choices=["deepseek-flash", "deepseek-v4-pro", "deepseek-pro"],
        help="DeepSeekモデル（デフォルト: deepseek-flash [$0.30/$1.20], 選択肢: deepseek-flash, deepseek-v4-pro / deepseek-pro [$0.43/$0.87]）"
    )

    p_chk = subparsers.add_parser("check", help="型チェック・Linter・テストの機械的実行")
    p_chk.add_argument("--path", default=None, help="リポジトリのパス")
    p_chk.add_argument("--build", action="store_true", help="Node/TSプロジェクトで npm run build を実行する")

    # Vision QA Subcommand
    p_vis = subparsers.add_parser("vision", help="Gemini 3.8 Flash Vision UI/Visual QA feedback loop")
    p_vis.add_argument("image", help="Path to UI screenshot image (PNG, JPEG, WebP)")
    p_vis.add_argument("--compare", default=None, help="Path to Figma/design reference mockup image")
    p_vis.add_argument("--prompt", default=None, help="Custom prompt or specific inspection instruction")
    p_vis.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (markdown|json)")
    p_vis.add_argument("--output", default=None, help="Output file path (default: stdout)")
    p_vis.add_argument("--model", default="gemini-2.5-flash", help="Gemini Vision model (default: gemini-2.5-flash)")
    p_vis.add_argument("--no-fail-on-issues", action="store_true", help="Do not exit with code 4 when visual issues are found")

    # Computer Use Subcommand
    p_cu = subparsers.add_parser("computer-use", help="Gemini 3.8 Flash Vision autonomous desktop automation")
    p_cu.add_argument("goal", help="The desktop task to perform (e.g. 'Open Notepad')")
    p_cu.add_argument("--image", default=None, help="Static image to perceive (useful for testing or headless runs)")
    p_cu.add_argument("--max-steps", type=int, default=10, help="Maximum execution steps (default: 10)")
    p_cu.add_argument("--dry-run", action="store_true", help="Perceive and plan actions without executing mouse/keys")
    p_cu.add_argument("--model", default="auto", help="Vision model (default: auto -> gemini/openai/claude)")

    # Fast Computer Use Subcommand
    p_fcu = subparsers.add_parser("fast-cu", help="Sub-100ms ultra-fast resident Computer Use daemon CLI")
    p_fcu.add_argument("action", choices=["ping", "screenshot", "focus", "batch"], help="Fast CU operation")
    p_fcu.add_argument("--title", default="", help="Window title query for focus")
    p_fcu.add_argument("--file", "-f", default=None, help="Batch action JSON file")
    p_fcu.add_argument("--json", default="[]", help="Batch action JSON string")

    # Options for English bridge
    p_rev.add_argument("--no-english-bridge", action="store_true", help="Bypass English optimization bridge")
    p_arch.add_argument("--no-english-bridge", action="store_true", help="Bypass English optimization bridge")
    p_arch.add_argument("--frugal", action="store_true", help="けち大学生モード：月額100円上限ガバナーを有効化し、予算超過時の有料SOTA呼び出しをブロック")

    p_log.add_argument("--no-english-bridge", action="store_true", help="Bypass English optimization bridge")
    p_log.add_argument("--frugal", action="store_true", help="けち大学生モード：月額100円上限ガバナーを有効化し、予算超過時の有料SOTA呼び出しをブロック")


    args = parser.parse_args()

    if args.command == "review":
        handle_review(args.path, model=args.model, deep=args.deep, thinking_budget=args.thinking_budget, dry_run=args.dry_run, frugal=getattr(args, 'frugal', False))
    elif args.command == "architect":
        problem_text = args.problem or ""
        if args.file:
            try:
                with open(args.file, "r", encoding="utf-8") as f:
                    file_content = f.read()
                if problem_text:
                    problem_text = f"{problem_text}\n\n{file_content}"
                else:
                    problem_text = file_content
            except Exception as e:
                print(f"[Error] プロンプトファイル '{args.file}' の読み込みに失敗しました: {e}", file=sys.stderr)
                sys.exit(EXIT_GATE_FAIL)

        if not problem_text.strip():
            print("[Error] 問題プロンプトが指定されていません（引数または --file で指定してください）。", file=sys.stderr)
            sys.exit(EXIT_USAGE_ERROR)

        handle_architect(problem_text, model=args.model, frugal=getattr(args, 'frugal', False))
    elif args.command == "logic":
        problem_text = args.problem or ""
        if args.file:
            try:
                with open(args.file, "r", encoding="utf-8") as f:
                    file_content = f.read()
                if problem_text:
                    problem_text = f"{problem_text}\n\n{file_content}"
                else:
                    problem_text = file_content
            except Exception as e:
                print(f"[Error] プロンプトファイル '{args.file}' の読み込みに失敗しました: {e}", file=sys.stderr)
                sys.exit(EXIT_GATE_FAIL)

        if not problem_text.strip():
            print("[Error] 問題プロンプトが指定されていません（引数または --file で指定してください）。", file=sys.stderr)
            sys.exit(EXIT_USAGE_ERROR)

        handle_logic(problem_text, model=args.model, frugal=getattr(args, 'frugal', False))
    elif args.command == "vision":
        code = handle_vision(
            image_path=args.image,
            compare_path=args.compare,
            prompt=args.prompt,
            format_type=args.format,
            output_file=args.output,
            model=args.model,
            fail_on_issues=not args.no_fail_on_issues,
        )
        sys.exit(code)
    elif args.command == "computer-use":
        code = handle_computer_use(
            goal=args.goal,
            max_steps=args.max_steps,
            dry_run=args.dry_run,
            model=args.model,
            image=args.image,
        )
        sys.exit(code)
    elif args.command == "fast-cu":
        code = handle_fast_cu(
            action_cmd=args.action,
            args_dict={"title": getattr(args, "title", ""), "file": getattr(args, "file", None), "json": getattr(args, "json", "[]")}
        )
        sys.exit(code)
    elif args.command == "check":
        code = handle_check(args.path, build=args.build)
        sys.exit(code)
    else:

        parser.print_help()


if __name__ == "__main__":
    main()
