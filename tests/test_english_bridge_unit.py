#!/usr/bin/env python3
"""
Unit tests for English Optimization Bridge and AI Team Gate Contracts.
Verifies:
1. Contracts: ExitCode, TaskType, Severity, IssueCategory, OptimizationResult, VisionQAIssue, VisionQAReport.
2. English Optimization Bridge: Token reduction >= 40% (target ~48.5%), template structure, entity extraction.
3. Bypass mechanisms: backward compatibility flag and optimize=False.
4. Code block preservation: code fences and diffs remain intact.
"""

import os
import sys
import unittest
from pathlib import Path

# Ensure directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_team_gate_contracts import (
    ExitCode,
    TaskType,
    Severity,
    IssueCategory,
    OptimizationResult,
    VisionQAIssue,
    VisionQAReport,
    EXIT_PASS,
    EXIT_GATE_FAIL,
    EXIT_USAGE_ERROR,
    EXIT_INPUT_ERROR,
    EXIT_VISUAL_ISSUES,
    EXIT_AUTH_CONFIG_ERROR,
)

from english_optimization_bridge import (
    EnglishOptimizationBridge,
    optimize_prompt,
    estimate_tokens,
    calculate_token_reduction,
    is_japanese,
)


class TestAiTeamGateContracts(unittest.TestCase):
    """Test standard types, enums, dataclasses, and exit codes."""

    def test_exit_codes(self):
        self.assertEqual(ExitCode.PASS, 0)
        self.assertEqual(ExitCode.GATE_FAIL, 1)
        self.assertEqual(ExitCode.USAGE_ERROR, 2)
        self.assertEqual(ExitCode.INPUT_ERROR, 3)
        self.assertEqual(ExitCode.VISUAL_ISSUES, 4)
        self.assertEqual(ExitCode.AUTH_CONFIG_ERROR, 5)

        self.assertEqual(EXIT_PASS, 0)
        self.assertEqual(EXIT_GATE_FAIL, 1)
        self.assertEqual(EXIT_USAGE_ERROR, 2)
        self.assertEqual(EXIT_INPUT_ERROR, 3)
        self.assertEqual(EXIT_VISUAL_ISSUES, 4)
        self.assertEqual(EXIT_AUTH_CONFIG_ERROR, 5)

    def test_task_types(self):
        self.assertEqual(TaskType.EXTERNAL_REVIEW, "external_review")
        self.assertEqual(TaskType.LOGIC, "logic")
        self.assertEqual(TaskType.ARCHITECT, "architect")

        self.assertEqual(TaskType.from_str("review"), TaskType.EXTERNAL_REVIEW)
        self.assertEqual(TaskType.from_str("external_review"), TaskType.EXTERNAL_REVIEW)
        self.assertEqual(TaskType.from_str("logic"), TaskType.LOGIC)
        self.assertEqual(TaskType.from_str("architect"), TaskType.ARCHITECT)
        with self.assertRaises(ValueError):
            TaskType.from_str("invalid_task_type")

    def test_severity_and_categories(self):
        self.assertEqual(Severity.CRITICAL, "critical")
        self.assertEqual(Severity.MAJOR, "major")
        self.assertEqual(Severity.MINOR, "minor")
        self.assertEqual(Severity.INFO, "info")

        self.assertEqual(IssueCategory.LAYOUT_BREAKAGE, "layout_breakage")
        self.assertEqual(IssueCategory.TEXT_OVERLAP, "text_overlap")
        self.assertEqual(IssueCategory.DESIGN_DIFF, "design_diff")
        self.assertEqual(IssueCategory.OTHER, "other")

    def test_optimization_result_serialization(self):
        result = OptimizationResult(
            original_prompt="元のプロンプト",
            optimized_prompt="Optimized prompt",
            task_type=TaskType.LOGIC,
            original_tokens=100,
            optimized_tokens=50,
            token_reduction_pct=50.0,
            is_bypassed=False,
            language="en",
            metadata={"test_key": "test_val"},
        )
        d = result.to_dict()
        self.assertEqual(d["task_type"], "logic")
        self.assertEqual(d["original_tokens"], 100)
        self.assertEqual(d["token_reduction_pct"], 50.0)

        # JSON round-trip
        j = result.to_json()
        self.assertIn("logic", j)
        reconstructed = OptimizationResult.from_dict(d)
        self.assertEqual(reconstructed.original_prompt, result.original_prompt)
        self.assertEqual(reconstructed.task_type, TaskType.LOGIC)

    def test_vision_qa_issue_and_report(self):
        issue1 = VisionQAIssue(
            category=IssueCategory.LAYOUT_BREAKAGE,
            severity=Severity.CRITICAL,
            description="Button overflows card boundary",
            selector="#submit-btn",
        )
        issue2 = VisionQAIssue(
            category=IssueCategory.TEXT_OVERLAP,
            severity=Severity.MINOR,
            description="Label overlaps hint icon",
        )

        report = VisionQAReport(passed=True)
        report.add_issue(issue1)
        self.assertFalse(report.passed)
        self.assertEqual(report.total_critical, 1)
        self.assertEqual(report.exit_code, ExitCode.VISUAL_ISSUES)

        report.add_issue(issue2)
        self.assertEqual(report.total_minor, 1)
        self.assertEqual(len(report.issues), 2)

        # Serialization
        d = report.to_dict()
        self.assertEqual(d["exit_code"], 4)
        reconstructed = VisionQAReport.from_dict(d)
        self.assertFalse(reconstructed.passed)
        self.assertEqual(reconstructed.total_critical, 1)


class TestEnglishOptimizationBridge(unittest.TestCase):
    """Test prompt optimization transformations, token reduction, and edge cases."""

    def setUp(self):
        # Ensure environment flag is clean for regular tests
        if "AI_TEAM_DISABLE_ENGLISH_OPTIMIZATION" in os.environ:
            del os.environ["AI_TEAM_DISABLE_ENGLISH_OPTIMIZATION"]

    def test_token_estimation(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("   "), 0)

        # Japanese detection
        self.assertTrue(is_japanese("日本語のプロンプトテストです。"))
        self.assertFalse(is_japanese("Strictly English prompt here."))

        # Japanese token estimation
        ja_text = "日本語のプロンプトテストです。計算量を最適化してください。"
        ja_tokens = estimate_tokens(ja_text)
        self.assertGreater(ja_tokens, 20)

        # English token estimation
        en_text = "Optimize computational complexity to O(N log N)."
        en_tokens = estimate_tokens(en_text)
        self.assertGreater(en_tokens, 5)

        # Token reduction calculation
        red = calculate_token_reduction(100, 50)
        self.assertEqual(red, 50.0)
        self.assertEqual(calculate_token_reduction(0, 50), 0.0)

    def test_review_prompt_transformation_and_token_reduction(self):
        """Verify EXTERNAL_REVIEW prompt transformation and >= 40% token reduction."""
        verbose_review_ja = (
            "以下のGit diffコード差分について敵対的コードレビューをお願いします。"
            "セキュリティ脆弱性、メモリリーク、例外処理の漏れ、型安全性の問題を厳しくチェックしてください。"
            "特に問題点があれば、重大度（CRITICAL, MAJOR, MINOR）を明記し、修正のためのコードスニペットを提示してください。"
            "問題がなければ承認、重大な欠陥があればリジェクトの判定をしてください。"
            "TypeScriptおよびFastAPIで記述されています。よろしくお願いします。"
        )

        res = optimize_prompt(verbose_review_ja, task_type=TaskType.EXTERNAL_REVIEW)

        # Structure checks
        self.assertIn("[CONTEXT]", res.optimized_prompt)
        self.assertIn("[TASK]", res.optimized_prompt)
        self.assertIn("[CONSTRAINTS]", res.optimized_prompt)
        self.assertIn("[OUTPUT_FORMAT]", res.optimized_prompt)

        # Domain checks
        self.assertIn("CRITICAL", res.optimized_prompt)
        self.assertIn("MAJOR", res.optimized_prompt)
        self.assertIn("PASS", res.optimized_prompt)
        self.assertIn("GATE_FAIL", res.optimized_prompt)
        self.assertIn("TypeScript", res.optimized_prompt)
        self.assertIn("FastAPI", res.optimized_prompt)

        # Token reduction verification (must be >= 40%)
        self.assertGreaterEqual(
            res.token_reduction_pct,
            40.0,
            f"Expected token reduction >= 40%, got {res.token_reduction_pct}%",
        )
        self.assertFalse(res.is_bypassed)

    def test_logic_prompt_transformation_and_token_reduction(self):
        """Verify LOGIC prompt transformation and >= 40% token reduction."""
        verbose_logic_ja = (
            "このPython関数は大量のデータを処理する際に非常に動作が遅く、計算量がO(N^2)になってしまっています。"
            "O(N log N)以下の効率的なアルゴリズムにリファクタリングしてください。"
            "また、マルチスレッド環境でも安全に動作するようにスレッドセーフな実装にし、競合状態を防止してください。"
            "エッジケース（空リスト、巨大データ、重複値）に対するユニットテストも合わせて作成してください。"
            "よろしくお願いします。"
        )

        res = optimize_prompt(verbose_logic_ja, task_type=TaskType.LOGIC)

        # Structure checks
        self.assertIn("[CONTEXT]", res.optimized_prompt)
        self.assertIn("[TASK]", res.optimized_prompt)
        self.assertIn("[CONSTRAINTS]", res.optimized_prompt)
        self.assertIn("[OUTPUT_FORMAT]", res.optimized_prompt)

        # Logic specifics
        self.assertIn("O(N^2)", res.optimized_prompt)
        self.assertIn("O(N log N)", res.optimized_prompt)
        self.assertIn("thread-safe", res.optimized_prompt.lower())

        # Token reduction verification (must be >= 40%)
        self.assertGreaterEqual(
            res.token_reduction_pct,
            40.0,
            f"Expected token reduction >= 40%, got {res.token_reduction_pct}%",
        )
        self.assertFalse(res.is_bypassed)

    def test_architect_prompt_transformation_and_token_reduction(self):
        """Verify ARCHITECT prompt transformation and >= 40% token reduction."""
        verbose_arch_ja = (
            "10万同時接続を処理するリアルタイム通知システムのアーキテクチャ設計をお願いします。"
            "FastAPIとRedis Pub/Sub、WebSocketを利用したスケーラブルな構成を検討しています。"
            "システム全体のデータフロー、ディレクトリ構成、各コンポーネントのインターフェース定義、および障害時のフォールバック設計を含めてください。"
            "また、複数の開発エージェントが並行して実装できるように、アトミックなサブタスクへの分解も作成してください。"
        )

        res = optimize_prompt(verbose_arch_ja, task_type=TaskType.ARCHITECT)

        # Structure checks
        self.assertIn("[CONTEXT]", res.optimized_prompt)
        self.assertIn("[TASK]", res.optimized_prompt)
        self.assertIn("[CONSTRAINTS]", res.optimized_prompt)
        self.assertIn("[OUTPUT_FORMAT]", res.optimized_prompt)

        # Architect specifics
        self.assertIn("100,000", res.optimized_prompt)
        self.assertIn("FastAPI", res.optimized_prompt)
        self.assertIn("Redis", res.optimized_prompt)
        self.assertIn("WebSocket", res.optimized_prompt)
        self.assertIn("Subagent", res.optimized_prompt)

        # Token reduction verification (must be >= 40%)
        self.assertGreaterEqual(
            res.token_reduction_pct,
            40.0,
            f"Expected token reduction >= 40%, got {res.token_reduction_pct}%",
        )
        self.assertFalse(res.is_bypassed)

    def test_average_token_reduction_target(self):
        """Verify across multiple tasks that average reduction hits target ~48.5%."""
        prompts = [
            ("以下のGit diffコード差分について敵対的コードレビューをお願いします。セキュリティ脆弱性、メモリリーク、例外処理の漏れ、型安全性の問題を厳しくチェックしてください。特に問題点があれば、重大度（CRITICAL, MAJOR, MINOR）を明記し、修正のためのコードスニペットを提示してください。問題がなければ承認、重大な欠陥があればリジェクトの判定をしてください。よろしくお願いします。", TaskType.EXTERNAL_REVIEW),
            ("このPython関数は大量のデータを処理する際に非常に動作が遅く、計算量がO(N^2)になってしまっています。O(N log N)以下の効率的なアルゴリズムにリファクタリングしてください。また、マルチスレッド環境でも安全に動作するようにスレッドセーフな実装にし、競合状態を防止してください。エッジケースに対するユニットテストも合わせて作成してください。よろしくお願いします。", TaskType.LOGIC),
            ("10万同時接続を処理するリアルタイム通知システムのアーキテクチャ設計をお願いします。FastAPIとRedis Pub/Sub、WebSocketを利用したスケーラブルな構成を検討しています。システム全体のデータフロー、ディレクトリ構成、各コンポーネントのインターフェース定義、および障害時のフォールバック設計を含めてください。また、アトミックなサブタスクへの分解も作成してください。", TaskType.ARCHITECT),
        ]

        reductions = []
        for prompt, task_type in prompts:
            res = optimize_prompt(prompt, task_type=task_type)
            reductions.append(res.token_reduction_pct)

        avg_reduction = sum(reductions) / len(reductions)
        print(f"\nAverage token reduction across tasks: {avg_reduction:.2f}% (Target: ~48.5%, Min: 40%)")
        self.assertGreaterEqual(avg_reduction, 40.0)

    def test_code_block_preservation(self):
        """Verify code blocks within prompt are preserved untouched."""
        prompt_with_code = (
            "以下のPythonコードのパフォーマンスを改善してください。\n"
            "```python\n"
            "def slow_func(data):\n"
            "    # Critical algorithm\n"
            "    return [x * 2 for x in data if x > 0]\n"
            "```\n"
            "O(N)で実装し直してください。"
        )

        res = optimize_prompt(prompt_with_code, task_type=TaskType.LOGIC)
        self.assertIn("def slow_func(data):", res.optimized_prompt)
        self.assertIn("return [x * 2 for x in data if x > 0]", res.optimized_prompt)

    def test_bypass_via_optimize_argument(self):
        """Verify optimize=False bypasses optimization."""
        orig = "変更しないでそのまま渡してください。"
        res = optimize_prompt(orig, task_type=TaskType.LOGIC, optimize=False)
        self.assertTrue(res.is_bypassed)
        self.assertEqual(res.optimized_prompt, orig)
        self.assertEqual(res.token_reduction_pct, 0.0)

    def test_bypass_via_environment_variable(self):
        """Verify AI_TEAM_DISABLE_ENGLISH_OPTIMIZATION=1 bypasses optimization."""
        orig = "環境変数でバイパスされるはずのプロンプトです。"
        try:
            os.environ["AI_TEAM_DISABLE_ENGLISH_OPTIMIZATION"] = "1"
            bridge = EnglishOptimizationBridge()
            res = bridge.optimize(orig, task_type=TaskType.ARCHITECT)
            self.assertTrue(res.is_bypassed)
            self.assertEqual(res.optimized_prompt, orig)
            self.assertEqual(res.token_reduction_pct, 0.0)
        finally:
            del os.environ["AI_TEAM_DISABLE_ENGLISH_OPTIMIZATION"]


if __name__ == "__main__":
    unittest.main()
