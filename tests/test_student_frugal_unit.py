"""
Unit tests for student_frugal_guide.py (けち大学生のAI開発環境)
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# multi-ai-team-setup ディレクトリを sys.path に追加
SETUP_DIR = Path(__file__).resolve().parent.parent
if str(SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(SETUP_DIR))

from student_frugal_guide import (  # noqa: E402
    BudgetGovernor,
    CostCalculator,
    DoctorCheck,
    main,
    normalize_model_name,
    simulate_student_month,
)
from ai_team_gate import check_frugal_guard, handle_review, record_frugal_spend  # noqa: E402


class TestCostCalculator:
    """CostCalculator の単体テスト"""

    def test_normalize_model_names(self):
        assert normalize_model_name("gemini-3.8-flash") == "gemini-3.8-flash"
        assert normalize_model_name("gemini-2.5-flash") == "gemini-2.5-flash"
        assert normalize_model_name("deepseek-flash") == "deepseek-flash"
        assert normalize_model_name("deepseek-v4-pro") == "deepseek-v4-pro"
        assert normalize_model_name("claude-opus-5-5") == "claude-opus-5-5"
        assert normalize_model_name("qwen2.5-coder") == "qwen2.5-coder"
        assert normalize_model_name("ollama-qwen-coder") == "local-ollama"
        assert normalize_model_name("local-reasoner") == "local-reasoner"

    def test_free_tier_cost_is_zero(self):
        calc = CostCalculator(usd_jpy_rate=155.0)
        for model in ["gemini-3.8-flash", "gemini-2.5-flash", "local-ollama", "local-reasoner"]:
            res = calc.calculate(model=model, input_tokens=10000, output_tokens=5000)
            assert res.is_free is True
            assert res.cost_usd == 0.0
            assert res.cost_jpy == 0.0

    def test_deepseek_flash_cost(self):
        calc = CostCalculator(usd_jpy_rate=150.0)
        # deepseek-flash: $0.30 in / $1.20 out per 1M
        # 10,000 in ($0.003) + 5,000 out ($0.006) = $0.009 * 150 = 1.35 JPY
        res = calc.calculate("deepseek-flash", input_tokens=10000, output_tokens=5000)
        assert res.is_free is False
        assert pytest.approx(res.cost_usd, rel=1e-4) == 0.009
        assert pytest.approx(res.cost_jpy, rel=1e-4) == 1.35

    def test_deepseek_flash_cache_hit_cost(self):
        calc = CostCalculator(usd_jpy_rate=150.0)
        # 10,000 input tokens total, 8,000 cache hit (at $0.075/1M), 2,000 normal (at $0.30/1M)
        # cache hit: 8,000 * 0.075 / 1M = 0.0006 USD
        # normal: 2,000 * 0.30 / 1M = 0.0006 USD
        # output: 1,000 * 1.20 / 1M = 0.0012 USD
        # Total USD = 0.0024 USD * 150 = 0.36 JPY
        res = calc.calculate(
            "deepseek-flash",
            input_tokens=10000,
            output_tokens=1000,
            cache_hit_tokens=8000,
        )
        assert pytest.approx(res.cost_usd, rel=1e-4) == 0.0024
        assert pytest.approx(res.cost_jpy, rel=1e-4) == 0.36

    def test_deepseek_v4_pro_cost(self):
        calc = CostCalculator(usd_jpy_rate=155.0)
        # deepseek-v4-pro: $0.43 in / $0.87 out per 1M
        res = calc.calculate("deepseek-v4-pro", input_tokens=1000, output_tokens=1000)
        expected_usd = (1000 * 0.43 + 1000 * 0.87) / 1_000_000.0
        assert pytest.approx(res.cost_usd, rel=1e-4) == expected_usd
        assert pytest.approx(res.cost_jpy, rel=1e-4) == expected_usd * 155.0

    def test_estimate_diff_audit_claude_opus(self):
        calc = CostCalculator(usd_jpy_rate=155.0)
        # diff 500 tokens in ($4.0/1M), 500 response tokens out ($20.0/1M)
        # 500 * 4 = 2000; 500 * 20 = 10000; total 12000 / 1M = 0.012 USD
        # 0.012 * 155 = 1.86 JPY
        res = calc.estimate_diff_audit("claude-opus-5-5", diff_tokens=500, response_tokens=500)
        assert pytest.approx(res.cost_usd, rel=1e-4) == 0.012
        assert pytest.approx(res.cost_jpy, rel=1e-4) == 1.86
        assert "diff" in res.description.lower()

    def test_negative_tokens_rejected(self):
        calc = CostCalculator(usd_jpy_rate=155.0)
        with pytest.raises(ValueError, match="Token counts must be non-negative"):
            calc.calculate("deepseek-flash", input_tokens=-1, output_tokens=100)
        with pytest.raises(ValueError, match="Token counts must be non-negative"):
            calc.calculate("deepseek-flash", input_tokens=100, output_tokens=-5)
        with pytest.raises(ValueError, match="Token counts must be non-negative"):
            calc.calculate("deepseek-flash", input_tokens=100, output_tokens=100, cache_hit_tokens=-10)

    def test_cache_hit_clamped_to_input(self):
        calc = CostCalculator(usd_jpy_rate=150.0)
        # cache_hit_tokens exceeds input_tokens -> clamped to input_tokens
        res = calc.calculate("deepseek-flash", input_tokens=1000, output_tokens=500, cache_hit_tokens=2000)
        assert res.cache_hit_tokens == 1000
        # effective input is 0, cache hit is 1000
        expected_usd = (1000 * 0.075 + 500 * 1.20) / 1_000_000.0
        assert pytest.approx(res.cost_usd, rel=1e-4) == expected_usd

    def test_safe_env_usd_jpy_rate(self, monkeypatch):
        # 不正な文字列が設定された場合はデフォルト(155.0)へ安全にフォールバック
        monkeypatch.setenv("USD_JPY_RATE", "invalid_rate")
        calc1 = CostCalculator()
        assert calc1.usd_jpy == 155.0

        # 0以下の値が設定された場合もデフォルトへフォールバック
        monkeypatch.setenv("USD_JPY_RATE", "-100.5")
        calc2 = CostCalculator()
        assert calc2.usd_jpy == 155.0


class TestBudgetGovernor:
    """BudgetGovernor の単体テスト"""

    @pytest.fixture
    def temp_ledger(self, tmp_path):
        ledger_file = tmp_path / ".frugal_ledger_test.json"
        return ledger_file

    def test_init_and_can_call_under_budget(self, temp_ledger):
        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)
        status = gov.get_status()
        assert status["budget_jpy"] == 100.0
        assert status["total_spent_jpy"] == 0.0
        assert status["remaining_jpy"] == 100.0
        assert status["is_exceeded"] is False

        # 有料モデル呼び出しの可否判定
        can_proceed, reason, est_cost = gov.can_call("claude-opus-5-5", estimated_input_tokens=500, estimated_output_tokens=500)
        assert can_proceed is True
        assert est_cost > 0.0
        assert "予算内" in reason

    def test_record_call_and_cumulative_spend(self, temp_ledger):
        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)
        entry1 = gov.record_call(
            model="deepseek-flash",
            input_tokens=2000,
            output_tokens=1000,
            description="unit test call 1",
        )
        assert entry1["cost_jpy"] > 0
        status1 = gov.get_status()
        assert status1["call_count"] == 1
        assert status1["total_spent_jpy"] == entry1["cost_jpy"]

        entry2 = gov.record_call(
            model="claude-opus-5-5",
            input_tokens=1000,
            output_tokens=500,
            description="unit test call 2",
        )
        status2 = gov.get_status()
        assert status2["call_count"] == 2
        assert status2["total_spent_jpy"] == pytest.approx(entry1["cost_jpy"] + entry2["cost_jpy"], rel=1e-3)

    def test_budget_exceeded_blocks_paid_calls_but_allows_free_calls(self, temp_ledger):
        # 予算上限をわずか 2.0 JPY に設定
        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=2.0)

        # 1回目の呼び出しで 1.86 JPY 消費
        gov.record_call("claude-opus-5-5", input_tokens=500, output_tokens=500)
        status = gov.get_status()
        assert status["total_spent_jpy"] > 1.5

        # 2回目のClaude Opus呼び出しは予算(2.0円)を超えるため拒否されるべき
        can_proceed, reason, _ = gov.can_call("claude-opus-5-5", estimated_input_tokens=500, estimated_output_tokens=500)
        assert can_proceed is False
        assert "月額予算上限" in reason
        assert "フォールバック" in reason

        # しかし、無料枠モデル（Gemini 3.8 Flash, Ollama）は予算超過時でも必ず許可される！
        free_can, free_reason, free_cost = gov.can_call("gemini-3.8-flash")
        assert free_can is True
        assert free_cost == 0.0
        assert "無料枠" in free_reason

        ollama_can, ollama_reason, ollama_cost = gov.can_call("local-ollama")
        assert ollama_can is True
        assert ollama_cost == 0.0

    def test_monthly_rollover(self, temp_ledger):
        # 過去の月のデータを注入
        old_data = {
            "month": "2025-10",
            "budget_jpy": 100.0,
            "total_spent_jpy": 85.5,
            "total_spent_usd": 0.55,
            "call_count": 15,
            "calls": [{"model": "deepseek-flash", "cost_jpy": 85.5}],
        }
        with open(temp_ledger, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)
        status = gov.get_status()

        # 現在の月へ自動リフレッシュされていること
        assert status["month"] == gov.current_month()
        assert status["total_spent_jpy"] == 0.0
        assert status["call_count"] == 0

        # 前月のサマリーが保存されていること
        ledger_raw = gov.load_ledger()
        assert "previous_month_summary" in ledger_raw
        assert ledger_raw["previous_month_summary"]["month"] == "2025-10"
        assert ledger_raw["previous_month_summary"]["total_spent_jpy"] == 85.5

    def test_reset_ledger(self, temp_ledger):
        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)
        gov.record_call("deepseek-flash", input_tokens=1000, output_tokens=1000)
        assert gov.get_status()["total_spent_jpy"] > 0
        gov.reset_ledger()
        assert gov.get_status()["total_spent_jpy"] == 0.0
        assert gov.get_status()["call_count"] == 0

    def test_corrupted_ledger_fail_closed_blocking(self, temp_ledger):
        # 破損したJSONファイルを書き込む
        temp_ledger.write_text("{corrupted_json_content...", encoding="utf-8")

        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)

        # 有料モデル呼び出しはフェイルクローズで即座にブロック
        can_proceed, reason, est_cost = gov.can_call("claude-opus-5-5")
        assert can_proceed is False
        assert est_cost == 0.0
        assert "台帳ファイルが破損しているため安全に有料APIをブロックしました（フェイルクローズ）。修復またはリセットを行ってください。" in reason
        assert gov.is_corrupted is True

        # バックアップファイルが作成されていること (.corrupted.<timestamp>)
        corrupted_backups = list(temp_ledger.parent.glob(f"{temp_ledger.name}.corrupted.*"))
        assert len(corrupted_backups) >= 1
        assert corrupted_backups[0].read_text(encoding="utf-8") == "{corrupted_json_content..."

        # 無料モデル呼び出しは破損時でも許可されること
        free_can, free_reason, free_cost = gov.can_call("gemini-3.8-flash")
        assert free_can is True
        assert free_cost == 0.0
        assert "完全無料枠モデル" in free_reason

        # 破損状態での記録呼び出しは拒否されること
        with pytest.raises(RuntimeError, match="破損しているため記録できません"):
            gov.record_call("claude-opus-5-5", 100, 100)

        # ステータスも破損状態を反映していること
        status = gov.get_status()
        assert status["is_corrupted"] is True
        assert status["is_exceeded"] is True

    def test_atomic_tmp_file_save(self, temp_ledger):
        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)
        test_data = {
            "month": gov.current_month(),
            "budget_jpy": 100.0,
            "total_spent_jpy": 10.0,
            "total_spent_usd": 0.065,
            "call_count": 1,
            "calls": [],
        }
        gov.save_ledger(test_data)

        # 台帳ファイルが正しく存在し内容が一致すること
        assert temp_ledger.exists()
        loaded = json.loads(temp_ledger.read_text(encoding="utf-8"))
        assert loaded["total_spent_jpy"] == 10.0

        # ディレクトリ内に孤立した .tmp 一時ファイルが残っていないこと
        leftover_tmps = list(temp_ledger.parent.glob(".frugal_*.tmp"))
        assert len(leftover_tmps) == 0

    def test_negative_token_and_cost_rejection(self, temp_ledger):
        gov = BudgetGovernor(ledger_path=temp_ledger, budget_jpy=100.0)
        with pytest.raises(ValueError, match="Token counts must be non-negative"):
            gov.record_call("deepseek-flash", input_tokens=-10, output_tokens=500)
        with pytest.raises(ValueError, match="Token counts must be non-negative"):
            gov.record_call("deepseek-flash", input_tokens=100, output_tokens=-5)
        with pytest.raises(ValueError, match="Token counts must be non-negative"):
            gov.record_call("deepseek-flash", input_tokens=100, output_tokens=100, cache_hit_tokens=-1)
        with pytest.raises(ValueError, match="cost_jpy cannot be negative"):
            gov.record_call("deepseek-flash", input_tokens=100, output_tokens=100, cost_jpy=-1.0, cost_usd=0.01)
        with pytest.raises(ValueError, match="cost_usd cannot be negative"):
            gov.record_call("deepseek-flash", input_tokens=100, output_tokens=100, cost_jpy=1.0, cost_usd=-0.01)

    def test_safe_env_budget_jpy(self, monkeypatch, temp_ledger):
        # 不正な文字列の場合はデフォルト(100.0)へフォールバック
        monkeypatch.setenv("FRUGAL_BUDGET_JPY", "corrupted_budget_string")
        gov1 = BudgetGovernor(ledger_path=temp_ledger)
        assert gov1.budget_jpy == 100.0

        # 0未満の値の場合はデフォルトへフォールバック
        monkeypatch.setenv("FRUGAL_BUDGET_JPY", "-50.0")
        gov2 = BudgetGovernor(ledger_path=temp_ledger)
        assert gov2.budget_jpy == 100.0


class TestDoctorCheck:
    """DoctorCheck の単体テスト"""

    def test_ollama_running(self):
        doc = DoctorCheck(ollama_url="http://localhost:11434")
        mock_response = io.BytesIO(
            json.dumps({
                "models": [
                    {"name": "qwen2.5-coder:1.5b-base-q8_0"},
                    {"name": "deepseek-r1:14b"},
                ]
            }).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            res = doc.check_ollama()
            assert res["status"] == "PASS"
            assert res["running"] is True
            assert res["models_count"] == 2
            assert res["has_coder"] is True
            assert res["has_reasoner"] is True

    def test_ollama_not_running(self):
        doc = DoctorCheck(ollama_url="http://invalid-host:9999")
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            res = doc.check_ollama()
            assert res["status"] == "WARN"
            assert res["running"] is False
            assert res["models_count"] == 0

    def test_check_keys(self, monkeypatch):
        doc = DoctorCheck()
        monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-deepseek-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        keys = doc.check_keys()
        assert keys["gemini"] is True
        assert keys["deepseek"] is True
        assert keys["anthropic"] is False
        assert keys["openai"] is False

    def test_diagnose_scoring(self, monkeypatch):
        doc = DoctorCheck()
        monkeypatch.setenv("GEMINI_API_KEY", "dummy")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
        with patch.object(doc, "check_ollama", return_value={"status": "PASS", "running": True, "models_count": 1, "models": ["qwen"]}):
            diag = doc.diagnose()
            assert diag["score"] >= 80
            assert diag["status"] == "EXCELLENT"


class TestSimulation:
    """simulate_student_month の単体テスト"""

    def test_simulate_under_100_jpy(self):
        sim = simulate_student_month(budget_jpy=100.0, days=30)
        assert sim["days"] == 30
        assert sim["budget_jpy"] == 100.0
        # 月間支出が 100 JPY 未満であることを保証
        assert sim["total_spent_jpy"] < 100.0
        assert sim["remaining_jpy"] > 0.0
        assert sim["savings_vs_subscription_jpy"] > 3000.0
        assert len(sim["activities"]) == 5


class TestCLIIntegration:
    """CLIコマンドライン実行のテスト"""

    def test_cli_status(self, tmp_path, capsys):
        ledger = tmp_path / "test_ledger.json"
        code = main(["status", "--ledger", str(ledger)])
        assert code == 0
        captured = capsys.readouterr().out
        assert "けち大学生のAI開発環境" in captured
        assert "月間予算ガバナーステータス" in captured

    def test_cli_status_json(self, tmp_path, capsys):
        ledger = tmp_path / "test_ledger.json"
        code = main(["status", "--ledger", str(ledger), "--json"])
        assert code == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert "budget_jpy" in data
        assert data["total_spent_jpy"] == 0.0

    def test_cli_calc(self, capsys):
        code = main(["calc", "--model", "claude-opus-5-5", "--diff", "500"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "ピンポイント・トークンコスト計算結果" in captured
        assert "claude-opus-5-5" in captured

    def test_cli_calc_json(self, capsys):
        code = main(["calc", "--model", "deepseek-flash", "--in", "1000", "--out", "1000", "--json"])
        assert code == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert data["model"] == "deepseek-flash"
        assert "cost_jpy" in data

    def test_cli_doctor(self, capsys):
        code = main(["doctor"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "リソース健全性診断" in captured

    def test_cli_simulate(self, capsys):
        code = main(["simulate", "--days", "30"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "けち大学生の月間開発シミュレーション" in captured
        assert "缶コーヒー1本" in captured

    def test_cli_check_call_allowed(self, tmp_path):
        ledger = tmp_path / "test_ledger.json"
        code = main(["check-call", "--model", "gemini-3.8-flash", "--ledger", str(ledger)])
        assert code == 0

    def test_cli_check_call_blocked_when_budget_exceeded(self, tmp_path):
        ledger = tmp_path / "test_ledger.json"
        gov = BudgetGovernor(ledger_path=ledger, budget_jpy=1.0)
        gov.record_call("claude-opus-5-5", input_tokens=500, output_tokens=500)  # ~1.86 JPY

        # Claude Opus call should now fail exit code 2
        code = main(["check-call", "--model", "claude-opus-5-5", "--ledger", str(ledger), "--in", "500", "--out", "500"])
        assert code == 2

    def test_cli_record(self, tmp_path, capsys):
        ledger = tmp_path / "test_ledger.json"
        code = main([
            "record",
            "--model", "deepseek-flash",
            "--in", "1000",
            "--out", "500",
            "--desc", "CLI test record",
            "--ledger", str(ledger),
        ])
        assert code == 0
        captured = capsys.readouterr().out
        assert "支出を台帳に記録しました" in captured


class TestGatekeeperFrugalIntegration:
    """ai_team_gate.py との連携テスト"""

    def test_check_frugal_guard_inactive(self):
        # frugalフラグも環境変数もOFFの場合、常に許可
        can_proceed, gov = check_frugal_guard("claude-opus-5-5", frugal=False)
        assert can_proceed is True
        assert gov is None

    def test_check_frugal_guard_active_under_budget(self, monkeypatch, tmp_path):
        ledger = tmp_path / "frugal_gate_ledger.json"
        test_gov = BudgetGovernor(ledger_path=ledger, budget_jpy=100.0)
        monkeypatch.setattr("ai_team_gate.BudgetGovernor", lambda: test_gov)

        can_proceed, gov = check_frugal_guard("deepseek-flash", est_in=1000, est_out=1000, frugal=True)
        assert can_proceed is True
        assert gov is not None

    def test_check_frugal_guard_active_over_budget_blocks_paid(self, monkeypatch, tmp_path, capsys):
        ledger = tmp_path / "frugal_gate_ledger.json"
        # 予算を 0.1 円に設定
        test_gov = BudgetGovernor(ledger_path=ledger, budget_jpy=0.1)
        test_gov.record_call("claude-opus-5-5", input_tokens=500, output_tokens=500)

        monkeypatch.setattr("ai_team_gate.BudgetGovernor", lambda: test_gov)

        # 有料モデルはブロックされる
        can_proceed, gov = check_frugal_guard("claude-opus-5-5", est_in=500, est_out=500, frugal=True)
        assert can_proceed is False
        captured = capsys.readouterr().err
        assert "月額100円上限ガードが作動しました" in captured
        assert "推奨アクション" in captured

    def test_record_frugal_spend(self, tmp_path, capsys):
        ledger = tmp_path / "frugal_gate_ledger.json"
        gov = BudgetGovernor(ledger_path=ledger, budget_jpy=100.0)

        record_frugal_spend(gov, "deepseek-flash", in_tok=1000, out_tok=1000, desc="test gate spend")
        status = gov.get_status()
        assert status["call_count"] == 1
        assert status["total_spent_jpy"] > 0
        captured = capsys.readouterr().out
        assert "Frugal Governor" in captured
        assert "当月利用額" in captured

    def test_check_frugal_guard_fail_closed_when_guide_missing(self, monkeypatch, capsys):
        monkeypatch.setattr("ai_team_gate.HAS_FRUGAL_GUIDE", False)
        with pytest.raises(SystemExit) as exc_info:
            check_frugal_guard("claude-opus-5-5", frugal=True)
        assert exc_info.value.code == 1
        captured = capsys.readouterr().err
        assert "[Error] --frugal が要求されましたが student_frugal_guide モジュールが読み込めないため、安全に停止します（フェイルクローズ）。" in captured

    def test_handle_review_records_frugal_spend_for_all_models(self, monkeypatch, tmp_path):
        ledger = tmp_path / "frugal_review_ledger.json"
        gov = BudgetGovernor(ledger_path=ledger, budget_jpy=100.0)

        monkeypatch.setattr("ai_team_gate.BudgetGovernor", lambda: gov)
        monkeypatch.setattr("ai_team_gate.run_cmd", lambda cmd, cwd=None: (0, "diff --git a/a.py b/a.py\n+val = 1\n", ""))
        monkeypatch.setattr("ai_team_gate.split_diff_blocks", lambda diff: [("a.py", diff)])

        # 1. Claude Opus review mock
        monkeypatch.setattr("ai_team_gate.ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("ai_team_gate.HAS_LITELLM", False)

        mock_claude_resp = io.BytesIO(
            json.dumps({
                "content": [{"type": "text", "text": "Claude diff audit OK"}],
                "usage": {"input_tokens": 300, "output_tokens": 150, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
            }).encode("utf-8")
        )
        with patch("urllib.request.urlopen", return_value=mock_claude_resp):
            handle_review(repo_path=str(tmp_path), model="claude-opus-5-5", frugal=True)

        status_claude = gov.get_status()
        assert status_claude["call_count"] == 1
        assert "claude-opus-5-5" in status_claude["models"]

        # 2. GPT review mock
        monkeypatch.setattr("ai_team_gate.OPENAI_API_KEY", "test-key")
        mock_gpt_resp = io.BytesIO(
            json.dumps({
                "choices": [{"message": {"content": "GPT diff audit OK"}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 200}
            }).encode("utf-8")
        )
        with patch("urllib.request.urlopen", return_value=mock_gpt_resp):
            handle_review(repo_path=str(tmp_path), model="gpt-6.1-sol", frugal=True)

        status_gpt = gov.get_status()
        assert status_gpt["call_count"] == 2
        assert "gpt-6-1-sol" in status_gpt["models"]

        # 3. Gemini review mock
        monkeypatch.setattr("ai_team_gate.GEMINI_API_KEY", "test-key")
        mock_gemini_resp = io.BytesIO(
            json.dumps({
                "candidates": [{"content": {"parts": [{"text": "Gemini review OK"}]}}],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 250}
            }).encode("utf-8")
        )
        with patch("urllib.request.urlopen", return_value=mock_gemini_resp):
            handle_review(repo_path=str(tmp_path), model="gemini-4-argon", frugal=True)

        status_gemini = gov.get_status()
        assert status_gemini["call_count"] == 3
        assert "gemini-4-argon" in status_gemini["models"]

