"""
Student Frugal Guide & Budget Governor (けち大学生のAI開発環境)
=============================================================
月額100円未満（缶コーヒー1本分未満）で最先端SOTAマルチAIチームを
フル活用するための予算統制（BudgetGovernor）、コスト計算（CostCalculator）、
ローカル/無料枠診断（DoctorCheck）、および月間利用シミュレーター。

Authors: Antigravity Multi-AI Team
License: MIT
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Windows cp932 エンコーディング対策
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# デフォルトUSD/JPY為替レート（環境変数 USD_JPY_RATE でオーバーライド可能）
DEFAULT_USD_JPY_RATE = 155.0

# けち大学生の月額予算上限（日本円: 缶コーヒー1本分未満 = 100円）
DEFAULT_FRUGAL_BUDGET_JPY = 100.0

# モデル別料金テーブル（USD per 1M tokens）
# input, output, cache_hit (prompt cache), is_free
MODEL_RATES: Dict[str, Dict[str, Any]] = {
    # 無料枠・ローカル枠（コスト 0円）
    "gemini-3.8-flash": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "is_free": True, "provider": "Google"},
    "gemini-2.5-flash": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "is_free": True, "provider": "Google"},
    "local-ollama": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "is_free": True, "provider": "Local GPU"},
    "local-reasoner": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "is_free": True, "provider": "Local GPU"},
    "local-autocomplete": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "is_free": True, "provider": "Local GPU"},
    "qwen2.5-coder": {"input": 0.0, "output": 0.0, "cache_hit": 0.0, "is_free": True, "provider": "Local GPU"},
    # 激安防衛ライン（DeepSeek）
    "deepseek-flash": {"input": 0.30, "output": 1.20, "cache_hit": 0.075, "is_free": False, "provider": "DeepSeek"},
    "deepseek-v4.1-flash": {"input": 0.30, "output": 1.20, "cache_hit": 0.075, "is_free": False, "provider": "DeepSeek"},
    "deepseek-v4-pro": {"input": 0.43, "output": 0.87, "cache_hit": 0.1075, "is_free": False, "provider": "DeepSeek"},
    "deepseek-pro": {"input": 0.43, "output": 0.87, "cache_hit": 0.1075, "is_free": False, "provider": "DeepSeek"},
    # SOTA監査・司令官枠（ピンポイント差分監査用）
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_hit": 0.40, "is_free": False, "provider": "Anthropic"},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_hit": 0.20, "is_free": False, "provider": "Anthropic"},
    "claude-haiku-5-5": {"input": 0.80, "output": 4.00, "cache_hit": 0.08, "is_free": False, "provider": "Anthropic"},
    "gpt-6-1-sol": {"input": 2.00, "output": 10.00, "cache_hit": 0.50, "is_free": False, "provider": "OpenAI"},
    "gpt-sol": {"input": 2.00, "output": 10.00, "cache_hit": 0.50, "is_free": False, "provider": "OpenAI"},
    "gemini-4-argon": {"input": 2.00, "output": 10.00, "cache_hit": 0.50, "is_free": False, "provider": "Google"},
    "gemini-4-carbon": {"input": 2.00, "output": 10.00, "cache_hit": 0.50, "is_free": False, "provider": "Google"},
}


def normalize_model_name(name: str) -> str:
    """モデル名を正規化する。"""
    normalized = name.lower().strip()
    if normalized in MODEL_RATES:
        return normalized
    # 部分一致・エイリアス対応
    if "flash" in normalized and ("gemini" in normalized or "3.8" in normalized or "2.5" in normalized):
        return "gemini-3.8-flash"
    if "ollama" in normalized or "qwen" in normalized or "local" in normalized:
        return "local-ollama"
    if "deepseek" in normalized and ("pro" in normalized or "v4-pro" in normalized):
        return "deepseek-v4-pro"
    if "deepseek" in normalized and "flash" in normalized:
        return "deepseek-flash"
    if "opus" in normalized:
        return "claude-opus-5-5"
    if "sonnet" in normalized:
        return "claude-sonnet-5-5"
    if "haiku" in normalized:
        return "claude-haiku-5-5"
    if "sol" in normalized or "gpt-6" in normalized:
        return "gpt-6-1-sol"
    if "argon" in normalized:
        return "gemini-4-argon"
    return normalized


@dataclass
class CostResult:
    """コスト計算結果。"""
    model: str
    is_free: bool
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int
    cost_usd: float
    cost_jpy: float
    usd_jpy_rate: float
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CostCalculator:
    """
    高精度トークンコスト計算器。
    Diff-Only Pinpoint則に基づき、1回あたりのAPI費用を円単位・ドル単位で算出する。
    """

    def __init__(self, usd_jpy_rate: Optional[float] = None) -> None:
        if usd_jpy_rate is not None:
            self.usd_jpy = float(usd_jpy_rate)
        else:
            self.usd_jpy = float(os.environ.get("USD_JPY_RATE", str(DEFAULT_USD_JPY_RATE)))

    def calculate(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
        description: str = "",
    ) -> CostResult:
        """指定モデルとトークン数から正確なコストを計算する。"""
        norm_model = normalize_model_name(model)
        rate_info = MODEL_RATES.get(norm_model)

        if not rate_info or rate_info.get("is_free", False):
            # 無料枠または未登録モデル（デフォルト無料とみなすか判定）
            if rate_info and rate_info.get("is_free"):
                return CostResult(
                    model=norm_model,
                    is_free=True,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_hit_tokens=cache_hit_tokens,
                    cost_usd=0.0,
                    cost_jpy=0.0,
                    usd_jpy_rate=self.usd_jpy,
                    description=description or "Free Tier / Local GPU (0 JPY)",
                )
            # 未登録モデルは一般的なフォールバック料金 ($2.00 / $10.00) を適用
            rate_info = {"input": 2.0, "output": 10.0, "cache_hit": 0.5, "is_free": False}

        p_in = float(rate_info["input"])
        p_out = float(rate_info["output"])
        p_cache = float(rate_info.get("cache_hit", p_in * 0.25))

        # キャッシュヒット分を差し引いた通常入力トークン
        effective_in = max(0, input_tokens - cache_hit_tokens)
        cost_usd = (effective_in * p_in + cache_hit_tokens * p_cache + output_tokens * p_out) / 1_000_000.0
        cost_jpy = cost_usd * self.usd_jpy

        return CostResult(
            model=norm_model,
            is_free=False,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cost_usd=cost_usd,
            cost_jpy=cost_jpy,
            usd_jpy_rate=self.usd_jpy,
            description=description,
        )

    def estimate_diff_audit(
        self,
        model: str = "claude-opus-5-5",
        diff_tokens: int = 500,
        response_tokens: int = 500,
        cache_hit_tokens: int = 0,
    ) -> CostResult:
        """
        Pinpoint Diff Audit（差分のみピンポイント送信）の想定コストを算出。
        例: Claude Opus 5.5 に 500 トークンの差分を送り 500 トークンの指摘を受ける場合:
        - 入力 500 * $4/1M = $0.002
        - 出力 500 * $20/1M = $0.010
        - 計 $0.012 * 155 = 約1.86円
        """
        desc = f"Pinpoint Diff Audit (~{diff_tokens} tokens diff)"
        return self.calculate(
            model=model,
            input_tokens=diff_tokens,
            output_tokens=response_tokens,
            cache_hit_tokens=cache_hit_tokens,
            description=desc,
        )


class BudgetGovernor:
    """
    月額予算ガバナー（BudgetGovernor）。
    月額100円の上限を厳密に管理し、予算超過時は有料SOTA呼び出しを強制遮断して
    無料枠（Gemini 3.8 Flash / ローカルOllama）へ安全に誘導する。
    """

    def __init__(
        self,
        ledger_path: Optional[Path | str] = None,
        budget_jpy: Optional[float] = None,
        calculator: Optional[CostCalculator] = None,
    ) -> None:
        if ledger_path is not None:
            self.ledger_path = Path(ledger_path)
        else:
            default_dir = Path(__file__).resolve().parent
            self.ledger_path = default_dir / ".frugal_ledger.json"

        if budget_jpy is not None:
            self.budget_jpy = float(budget_jpy)
        else:
            env_val = os.environ.get("FRUGAL_BUDGET_JPY")
            self.budget_jpy = float(env_val) if env_val else DEFAULT_FRUGAL_BUDGET_JPY

        self.calculator = calculator or CostCalculator()

    @staticmethod
    def current_month() -> str:
        """現在の年月 (YYYY-MM) を返す。"""
        return datetime.datetime.now().strftime("%Y-%m")

    def load_ledger(self) -> Dict[str, Any]:
        """台帳 JSON を読み込む。月が替わっている場合は自動で当月用に初期化する。"""
        now_month = self.current_month()
        if not self.ledger_path.exists():
            return self._init_new_ledger(now_month)

        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("month") != now_month:
                # 月が替わったため当月用にアーカイブ＆リフレッシュ
                return self._init_new_ledger(now_month, previous_record=data)
            return data
        except Exception:
            # 破損等の場合は新規初期化
            return self._init_new_ledger(now_month)

    def _init_new_ledger(self, month: str, previous_record: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """新規月の台帳を作成して保存する。"""
        data: Dict[str, Any] = {
            "month": month,
            "budget_jpy": self.budget_jpy,
            "total_spent_jpy": 0.0,
            "total_spent_usd": 0.0,
            "call_count": 0,
            "calls": [],
        }
        if previous_record and "month" in previous_record:
            data["previous_month_summary"] = {
                "month": previous_record.get("month"),
                "total_spent_jpy": previous_record.get("total_spent_jpy", 0.0),
                "call_count": previous_record.get("call_count", 0),
            }
        self.save_ledger(data)
        return data

    def save_ledger(self, data: Dict[str, Any]) -> None:
        """台帳を安全にアトミック保存する。"""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.ledger_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.ledger_path)

    def can_call(
        self,
        model: str,
        estimated_input_tokens: int = 500,
        estimated_output_tokens: int = 500,
    ) -> Tuple[bool, str, float]:
        """
        API呼び出しを実行可能か判定する。
        - 無料枠・ローカル枠: 予算に関わらず常に True (0.0 JPY)
        - 有料SOTA枠: 当月の累計支出 + 見積もり費用 <= 予算上限 かどうかをチェック。
        返り値: (実行可否, 理由説明, 見積もり費用JPY)
        """
        norm_model = normalize_model_name(model)
        rate_info = MODEL_RATES.get(norm_model, {})

        if rate_info.get("is_free", False):
            return True, f"完全無料枠モデル（{norm_model}）のため無制限に実行可能です。", 0.0

        est_res = self.calculator.calculate(
            model=norm_model,
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
        )
        est_cost_jpy = est_res.cost_jpy

        ledger = self.load_ledger()
        current_spent = float(ledger.get("total_spent_jpy", 0.0))
        budget = float(ledger.get("budget_jpy", self.budget_jpy))

        if current_spent + est_cost_jpy > budget:
            reason = (
                f"月額予算上限（{budget:.1f}円）を超過します！\n"
                f"  - 現在の当月利用額: {current_spent:.2f}円\n"
                f"  - 見積もり呼び出し費用: {est_cost_jpy:.2f}円（{norm_model}）\n"
                f"  - 想定合計: {current_spent + est_cost_jpy:.2f}円 > {budget:.1f}円\n"
                f"有料SOTA APIの実行をブロックしました。無料枠（Gemini 3.8 Flash / ローカルOllama）へフォールバックしてください。"
            )
            return False, reason, est_cost_jpy

        remaining = budget - (current_spent + est_cost_jpy)
        reason = f"予算内です（残高想定: {remaining:.2f}円 / 上限: {budget:.1f}円）。"
        return True, reason, est_cost_jpy

    def record_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
        cost_jpy: Optional[float] = None,
        cost_usd: Optional[float] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """実行完了したAPI呼び出しの支出を記録する。"""
        norm_model = normalize_model_name(model)

        if cost_jpy is None or cost_usd is None:
            res = self.calculator.calculate(
                model=norm_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit_tokens=cache_hit_tokens,
                description=description,
            )
            c_jpy = res.cost_jpy
            c_usd = res.cost_usd
        else:
            c_jpy = float(cost_jpy)
            c_usd = float(cost_usd)

        ledger = self.load_ledger()
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "model": norm_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cost_jpy": round(c_jpy, 4),
            "cost_usd": round(c_usd, 6),
            "description": description,
        }

        ledger["calls"].append(entry)
        ledger["total_spent_jpy"] = round(float(ledger.get("total_spent_jpy", 0.0)) + c_jpy, 4)
        ledger["total_spent_usd"] = round(float(ledger.get("total_spent_usd", 0.0)) + c_usd, 6)
        ledger["call_count"] = int(ledger.get("call_count", 0)) + 1

        self.save_ledger(ledger)
        return entry

    def get_status(self) -> Dict[str, Any]:
        """現在の月額予算利用状況サマリーを取得する。"""
        ledger = self.load_ledger()
        spent_jpy = float(ledger.get("total_spent_jpy", 0.0))
        budget = float(ledger.get("budget_jpy", self.budget_jpy))
        remaining = max(0.0, budget - spent_jpy)
        usage_pct = (spent_jpy / budget * 100.0) if budget > 0 else 0.0

        # モデル別集計
        model_breakdown: Dict[str, Dict[str, Any]] = {}
        for c in ledger.get("calls", []):
            m = c.get("model", "unknown")
            if m not in model_breakdown:
                model_breakdown[m] = {"calls": 0, "jpy": 0.0, "tokens": 0}
            model_breakdown[m]["calls"] += 1
            model_breakdown[m]["jpy"] += float(c.get("cost_jpy", 0.0))
            model_breakdown[m]["tokens"] += int(c.get("input_tokens", 0)) + int(c.get("output_tokens", 0))

        return {
            "month": ledger.get("month", self.current_month()),
            "budget_jpy": budget,
            "total_spent_jpy": round(spent_jpy, 4),
            "total_spent_usd": round(float(ledger.get("total_spent_usd", 0.0)), 6),
            "remaining_jpy": round(remaining, 4),
            "usage_percent": round(usage_pct, 1),
            "call_count": ledger.get("call_count", len(ledger.get("calls", []))),
            "is_exceeded": spent_jpy >= budget,
            "models": model_breakdown,
        }

    def reset_ledger(self, month: Optional[str] = None) -> None:
        """台帳を初期化する（テストまたは強制リセット用）。"""
        target_month = month or self.current_month()
        self._init_new_ledger(target_month)


class DoctorCheck:
    """
    ローカルおよびクラウド無料枠のリソース健全性診断器。
    学生が完全無料または超低コストで開発環境を稼働できるかを瞬時にチェックする。
    """

    def __init__(self, ollama_url: str = "http://localhost:11434") -> None:
        self.ollama_url = ollama_url.rstrip("/")

    def check_ollama(self) -> Dict[str, Any]:
        """ローカルOllamaサービスの接続とモデル有無を検査する。"""
        endpoint = f"{self.ollama_url}/api/tags"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name", "") for m in payload.get("models", [])]
                    has_qwen = any("qwen" in m.lower() for m in models)
                    has_coder = any("coder" in m.lower() for m in models)
                    has_r1 = any("r1" in m.lower() for m in models)

                    return {
                        "status": "PASS",
                        "running": True,
                        "url": self.ollama_url,
                        "models_count": len(models),
                        "models": models,
                        "has_coder": has_qwen or has_coder,
                        "has_reasoner": has_r1,
                        "message": f"Ollama稼働中（{len(models)}モデル検出）。Tab補完&ローカル推論が無料利用可能！",
                    }
        except Exception as e:
            return {
                "status": "WARN",
                "running": False,
                "url": self.ollama_url,
                "models_count": 0,
                "models": [],
                "has_coder": False,
                "has_reasoner": False,
                "message": f"Ollamaが検出されませんでした（{e}）。エディタTab補完を0円で行うには Ollama の起動を推奨します。",
            }
        return {"status": "FAIL", "running": False, "message": "Unknown status"}

    def check_keys(self) -> Dict[str, Any]:
        """APIキーの設定状況を診断する。"""
        # .env ファイルからの読み込みも試行
        keys_status = {
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "deepseek": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
        }
        return keys_status

    def diagnose(self) -> Dict[str, Any]:
        """総合診断レポートを作成する。"""
        ollama_info = self.check_ollama()
        keys = self.check_keys()

        score = 100
        recommendations: List[str] = []

        if not keys["gemini"]:
            score -= 35
            recommendations.append("【最優先】Gemini APIキーを設定してください（Google AI Studioで無料発行可能。日常開発が完全0円になります）。")

        if not ollama_info.get("running"):
            score -= 20
            recommendations.append("【推奨】Ollamaを起動し `qwen2.5-coder:1.5b` をプルすると、VS Code等のTab補完が完全無料・遅延20msで動きます。")

        if not keys["deepseek"]:
            score -= 15
            recommendations.append("【高コスパ】DeepSeek APIキーを設定すると、1回約0.05円で競技レベルの難関バグを高速解決できます。")

        if not keys["anthropic"]:
            recommendations.append("【任意】マイルストーン完了時の最終防衛線として Claude Opus 5.5（差分監査1回1〜2円）のキーを準備すると完璧です。")

        return {
            "score": max(0, score),
            "status": "EXCELLENT" if score >= 80 else ("GOOD" if score >= 60 else "NEEDS_SETUP"),
            "ollama": ollama_info,
            "api_keys": keys,
            "recommendations": recommendations,
        }


def simulate_student_month(budget_jpy: float = DEFAULT_FRUGAL_BUDGET_JPY, days: int = 30) -> Dict[str, Any]:
    """
    けち大学生の月間開発コストシミュレーション。
    大学の課題、研究室の実験コード、個人開発を毎日ガシガシ回した場合の
    リアルなトークン消費と費用内訳を算出する。
    """
    calc = CostCalculator()

    # 1. ローカルTab補完 (RTX 4070S Ollama)
    tab_calls = 200 * days  # 200回/日 = 6,000回
    tab_cost = 0.0

    # 2. 日常のコーディング・テスト実行 (Gemini 3.8 Flash High)
    flash_calls = 30 * days  # 30回/日 = 900回
    flash_cost = 0.0

    # 3. 論文・仕様調査・グラウンディング (Gemini Notebook / NotebookLM MCP)
    notebook_queries = 10 * days  # 10回/日 = 300回
    notebook_cost = 0.0

    # 4. 難関バグ・競合状態の解決 (DeepSeek V4.1 Flash & V4 Pro)
    # 月に12回スタックが発生し、DeepSeekへ相談（1回あたり入力800 tok, 出力1200 tok）
    ds_calls = 12
    ds_res = calc.calculate("deepseek-v4-pro", input_tokens=800, output_tokens=1200)
    ds_total_jpy = ds_res.cost_jpy * ds_calls

    # 5. マイルストーン最終差分監査 (Claude Opus 5.5 / Extended Thinking)
    # 機能完成時のGit差分のみピンポイント監査（月に3回、1回あたり入力600 tok, 出力500 tok）
    opus_calls = 3
    opus_res = calc.estimate_diff_audit("claude-opus-5-5", diff_tokens=600, response_tokens=500)
    opus_total_jpy = opus_res.cost_jpy * opus_calls

    total_spent_jpy = tab_cost + flash_cost + notebook_cost + ds_total_jpy + opus_total_jpy
    remaining_jpy = budget_jpy - total_spent_jpy

    # 缶コーヒー価格（自販機で約140円）との比較
    canned_coffee_price = 140.0
    vs_chatgpt_plus = 3100.0  # ChatGPT Plus $20/月 (約3,100円)

    return {
        "days": days,
        "budget_jpy": budget_jpy,
        "total_spent_jpy": round(total_spent_jpy, 2),
        "remaining_jpy": round(remaining_jpy, 2),
        "canned_coffee_price": canned_coffee_price,
        "savings_vs_subscription_jpy": round(vs_chatgpt_plus - total_spent_jpy, 2),
        "activities": [
            {
                "tier": "Tier 5 (Local GPU)",
                "task": "VS CodeリアルタイムTab補完 (Qwen 2.5 Coder 1.5B)",
                "volume": f"{tab_calls:,} 回 ({tab_calls // days}回/日)",
                "cost_jpy": "0.00円",
                "provider": "自宅GPU (Ollama)",
            },
            {
                "tier": "Tier 4 (Executor)",
                "task": "日常のコード生成・修正・単体テスト (Gemini 3.8 Flash High)",
                "volume": f"{flash_calls:,} 回 ({flash_calls // days}回/日)",
                "cost_jpy": "0.00円",
                "provider": "Google AI Studio 無料枠",
            },
            {
                "tier": "HQ (Ground Truth)",
                "task": "仕様書・論文・ライブラリ公式ドキュメント調査 (Gemini Notebook)",
                "volume": f"{notebook_queries:,} 回 ({notebook_queries // days}回/日)",
                "cost_jpy": "0.00円",
                "provider": "NotebookLM MCP (無料枠内)",
            },
            {
                "tier": "Tier 1 & 2 (Defense)",
                "task": "難関アルゴリズム・デバッグ解決 (DeepSeek V4 Pro)",
                "volume": f"{ds_calls} 回 (1回あたり約{ds_res.cost_jpy:.2f}円)",
                "cost_jpy": f"{ds_total_jpy:.2f}円",
                "provider": "DeepSeek API",
            },
            {
                "tier": "Tier 3 (Red Team)",
                "task": "マイルストーン完了時の敵対的差分セキュリティ監査 (Claude Opus 5.5)",
                "volume": f"{opus_calls} 回 (1回あたり約{opus_res.cost_jpy:.2f}円)",
                "cost_jpy": f"{opus_total_jpy:.2f}円",
                "provider": "Anthropic API (Diff-Only)",
            },
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLIエントリーポイント。"""
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--budget", type=float, default=None, help=f"月額予算上限(円)（デフォルト: {DEFAULT_FRUGAL_BUDGET_JPY}円）")
    common_parser.add_argument("--ledger", type=str, default=None, help="台帳JSONファイルのパス（デフォルト: .frugal_ledger.json）")
    common_parser.add_argument("--json", action="store_true", help="JSON形式で出力")

    parser = argparse.ArgumentParser(
        description="けち大学生のAI開発環境（月額100円未満・超コスパSOTAマルチAI運用ツール）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", parents=[common_parser], help="当月の予算利用状況と支出内訳を表示")

    # calc
    p_calc = subparsers.add_parser("calc", parents=[common_parser], help="指定モデル・トークン数の呼び出しコストを計算")
    p_calc.add_argument("--model", "-m", default="claude-opus-5-5", help="モデルID（デフォルト: claude-opus-5-5）")
    p_calc.add_argument("--in-tokens", "--in", type=int, default=500, help="入力トークン数（デフォルト: 500）")
    p_calc.add_argument("--out-tokens", "--out", type=int, default=500, help="出力トークン数（デフォルト: 500）")
    p_calc.add_argument("--cache-hit", type=int, default=0, help="プロンプトキャッシュヒット数（デフォルト: 0）")
    p_calc.add_argument("--diff", type=int, default=None, help="差分トークン数（指定時はPinpoint Diff Auditとして算出）")

    # doctor
    subparsers.add_parser("doctor", parents=[common_parser], help="無料枠・ローカルOllamaの稼働状況を診断")

    # simulate
    p_sim = subparsers.add_parser("simulate", parents=[common_parser], help="学生の月間開発利用シミュレーション（缶コーヒー比較）を実行")
    p_sim.add_argument("--days", type=int, default=30, help="シミュレーション日数（デフォルト: 30日）")

    # check-call
    p_chk = subparsers.add_parser("check-call", parents=[common_parser], help="特定のAPI呼び出しが月額予算内で許可されるか判定")
    p_chk.add_argument("--model", "-m", required=True, help="モデルID")
    p_chk.add_argument("--in-tokens", "--in", type=int, default=500, help="見積もり入力トークン数")
    p_chk.add_argument("--out-tokens", "--out", type=int, default=500, help="見積もり出力トークン数")

    # record
    p_rec = subparsers.add_parser("record", parents=[common_parser], help="API呼び出しの支出を台帳に記録")
    p_rec.add_argument("--model", "-m", required=True, help="モデルID")
    p_rec.add_argument("--in-tokens", "--in", type=int, required=True, help="実績入力トークン数")
    p_rec.add_argument("--out-tokens", "--out", type=int, required=True, help="実績出力トークン数")
    p_rec.add_argument("--cache-hit", type=int, default=0, help="キャッシュヒット数")
    p_rec.add_argument("--desc", default="", help="呼び出し概要・目的")

    args = parser.parse_args(argv)

    governor = BudgetGovernor(ledger_path=args.ledger, budget_jpy=args.budget)
    calc = CostCalculator()

    if args.command == "status":
        status = governor.get_status()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print("=" * 60)
            print(f"🎓 けち大学生のAI開発環境 - 月間予算ガバナーステータス ({status['month']})")
            print("=" * 60)
            print(f"💰 月額予算上限 : {status['budget_jpy']:.1f} 円 （缶コーヒー1本未満）")
            print(f"💳 当月利用総額 : {status['total_spent_jpy']:.2f} 円 (${status['total_spent_usd']:.4f})")
            print(f"🏷  残り予算残高 : {status['remaining_jpy']:.2f} 円 (消化率: {status['usage_percent']}%)")
            print(f"📞 SOTA呼び出し回数: {status['call_count']} 回")
            print(f"🚨 予算超過状態 : {'【警告】上限超過！無料枠へ強制移行中' if status['is_exceeded'] else '【健全】予算内で快調に稼働中'}")
            print("-" * 60)
            if status["models"]:
                print("【モデル別支出内訳】")
                for m, info in status["models"].items():
                    print(f"  - {m:<18}: {info['calls']}回呼び出し | {info['jpy']:.2f}円 ({info['tokens']} tokens)")
            else:
                print("当月の有料API支出はまだありません（無料枠のみで完璧に防衛中）。")
            print("=" * 60)
        return 0

    elif args.command == "calc":
        if args.diff is not None:
            res = calc.estimate_diff_audit(
                model=args.model,
                diff_tokens=args.diff,
                response_tokens=args.out_tokens,
                cache_hit_tokens=args.cache_hit,
            )
        else:
            res = calc.calculate(
                model=args.model,
                input_tokens=args.in_tokens,
                output_tokens=args.out_tokens,
                cache_hit_tokens=args.cache_hit,
            )

        if args.json:
            print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        else:
            print("=" * 60)
            print("📊 ピンポイント・トークンコスト計算結果")
            print("=" * 60)
            print(f"モデル名    : {res.model} ({'完全無料' if res.is_free else '従量課金'})")
            print(f"トークン内訳: 入力 {res.input_tokens:,} | 出力 {res.output_tokens:,} | キャッシュヒット {res.cache_hit_tokens:,}")
            print(f"概算費用    : {res.cost_jpy:.2f} 円 (${res.cost_usd:.5f}) [為替: 1 USD = {res.usd_jpy_rate:.1f} JPY]")
            if res.description:
                print(f"説明        : {res.description}")
            print("=" * 60)
        return 0

    elif args.command == "doctor":
        doc = DoctorCheck()
        diag = doc.diagnose()
        if args.json:
            print(json.dumps(diag, ensure_ascii=False, indent=2))
        else:
            print("=" * 60)
            print(f"🩺 けち大学生のAI開発環境 - リソース健全性診断 (スコア: {diag['score']}/100 - {diag['status']})")
            print("=" * 60)
            ollama = diag["ollama"]
            print("【ローカルGPU環境 (Ollama)】")
            if ollama.get("running"):
                print(f"  ✅ 稼働中 ({ollama['url']}) - 検出モデル数: {ollama['models_count']}")
                print(f"     利用可能モデル: {', '.join(ollama['models'])}")
            else:
                print(f"  ⚠️ 未稼働 ({ollama['message']})")

            keys = diag["api_keys"]
            print("\n【APIキー設定状況】")
            print(f"  {'✅' if keys['gemini'] else '❌'} Google/Gemini APIキー   : {'設定済み (日常開発0円枠有効)' if keys['gemini'] else '未設定 (要登録)'}")
            print(f"  {'✅' if keys['deepseek'] else '⚠️'} DeepSeek APIキー        : {'設定済み (1回0.05円防衛線有効)' if keys['deepseek'] else '未設定 (格安解決に推奨)'}")
            print(f"  {'✅' if keys['anthropic'] else '⚪'} Anthropic APIキー       : {'設定済み (マイルストーン差分監査)' if keys['anthropic'] else '未設定 (任意)'}")
            print(f"  {'✅' if keys['openai'] else '⚪'} OpenAI APIキー          : {'設定済み (GPT-Sol数学監査)' if keys['openai'] else '未設定 (任意)'}")

            if diag["recommendations"]:
                print("\n【診断からのアドバイス】")
                for r in diag["recommendations"]:
                    print(f"  💡 {r}")
            print("=" * 60)
        return 0

    elif args.command == "simulate":
        budget = args.budget or governor.budget_jpy
        sim = simulate_student_month(budget_jpy=budget, days=args.days)
        if args.json:
            print(json.dumps(sim, ensure_ascii=False, indent=2))
        else:
            print("=" * 68)
            print(f"🎓 けち大学生の月間開発シミュレーション（{sim['days']}日間・毎日フル稼働）")
            print("=" * 68)
            print(f"設 定 予 算 : {sim['budget_jpy']:.0f} 円 （缶コーヒー1本 {sim['canned_coffee_price']:.0f}円 より安い！）")
            print(f"想定月間支出: {sim['total_spent_jpy']:.2f} 円 （残高: {sim['remaining_jpy']:.2f} 円）")
            print(f"サブスク比較: ChatGPT Plus (月3,100円) より毎月 【{sim['savings_vs_subscription_jpy']:.0f} 円】 浮く！")
            print("-" * 68)
            print(f"{'開発タスク / 階層':<32} | {'月間処理量':<18} | {'月間費用'}")
            print("-" * 68)
            for act in sim["activities"]:
                task_name = f"{act['tier']}: {act['task'][:18]}..."
                print(f"{task_name:<34} | {act['volume']:<18} | {act['cost_jpy']}")
            print("=" * 68)
            print("結論: 日常の95%を無料枠で捌き、難所だけピンポイントで数円課金すれば、")
            print("      月額100円未満で最高峰SOTAマルチAI環境を使い倒せます！")
            print("=" * 68)
        return 0

    elif args.command == "check-call":
        can_proceed, reason, est_cost = governor.can_call(
            model=args.model,
            estimated_input_tokens=args.in_tokens,
            estimated_output_tokens=args.out_tokens,
        )
        res_payload = {
            "can_call": can_proceed,
            "model": args.model,
            "estimated_cost_jpy": round(est_cost, 4),
            "reason": reason,
        }
        if args.json:
            print(json.dumps(res_payload, ensure_ascii=False, indent=2))
        else:
            print(f"判定結果: {'[許可 (ALLOW)]' if can_proceed else '[拒否 (DENY)]'}")
            print(f"見積費用: {est_cost:.2f} 円")
            print(f"理由    : {reason}")
        return 0 if can_proceed else 2

    elif args.command == "record":
        entry = governor.record_call(
            model=args.model,
            input_tokens=args.in_tokens,
            output_tokens=args.out_tokens,
            cache_hit_tokens=args.cache_hit,
            description=args.desc,
        )
        if args.json:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        else:
            print(f"✅ 支出を台帳に記録しました: {entry['model']} - {entry['cost_jpy']:.2f}円 (${entry['cost_usd']:.5f})")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
