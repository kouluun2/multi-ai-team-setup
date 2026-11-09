#!/usr/bin/env python3
"""
Vision QA Report Generator.
Formats Gemini Flash Vision audit results into structured Markdown reports
or clean JSON output for CI/CD and developer feedback loops.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

# Prevent Unicode/Emoji encoding errors on Windows console (e.g. CP932 / Shift-JIS)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from vision_client import GeminiFlashVisionClient


# ---------------------------------------------------------------------------
# Formatting Helpers
# ---------------------------------------------------------------------------

SEVERITY_BADGES = {
    "critical": "🔴 **[CRITICAL]**",
    "major": "🟠 **[MAJOR]**",
    "minor": "🟡 **[MINOR]**",
    "info": "🔵 **[INFO]**",
}

CATEGORY_NAMES = {
    "layout_breakage": "📐 Layout Breakage / Clipping",
    "text_overlap": "🔤 Text Overlap / Contrast",
    "design_diff": "🎨 Design vs Implementation Diff",
    "other": "🔍 Visual Polish / Other",
}

STATUS_BADGES = {
    "ok": "🟢 **PASS (No visual defects found)**",
    "issues_found": "🔴 **ACTION REQUIRED (Visual issues detected)**",
    "error": "⚠️ **AUDIT ERROR (Failed to complete audit)**",
}


def format_markdown_report(result: Dict[str, Any], title: str = "Visual QA Audit Report") -> str:
    """
    Format audit result dictionary into a professional GitHub-flavored Markdown report.
    """
    status = result.get("status", "unknown").lower()
    status_header = STATUS_BADGES.get(status, f"❓ **UNKNOWN STATUS ({status})**")

    summary = result.get("summary", {})
    critical_cnt = summary.get("critical", 0)
    major_cnt = summary.get("major", 0)
    minor_cnt = summary.get("minor", 0)
    info_cnt = summary.get("info", 0)
    total_issues = critical_cnt + major_cnt + minor_cnt + info_cnt

    image_path = result.get("image_path", "N/A")
    compare_path = result.get("compare_image_path")
    model = result.get("model", "Gemini Flash Vision")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: List[str] = [
        f"# 📋 {title}",
        "",
        f"- **Audit Status**: {status_header}",
        f"- **Audited Image**: `{image_path}`",
    ]

    if compare_path:
        lines.append(f"- **Design Reference**: `{compare_path}`")

    lines.extend([
        f"- **Model**: `{model}`",
        f"- **Timestamp**: `{timestamp}`",
        "",
        "## 📊 Summary",
        "",
        "| Severity | Count | Status |",
        "| :--- | :---: | :--- |",
        f"| 🔴 Critical | {critical_cnt} | {'⚠️ Immediate Fix Required' if critical_cnt > 0 else '✅ None'} |",
        f"| 🟠 Major | {major_cnt} | {'⚠️ High Priority Fix' if major_cnt > 0 else '✅ None'} |",
        f"| 🟡 Minor | {minor_cnt} | {'ℹ️ Polish / Tweak' if minor_cnt > 0 else '✅ None'} |",
        f"| 🔵 Info | {info_cnt} | {'ℹ️ Suggestion' if info_cnt > 0 else '✅ None'} |",
        f"| **Total Issues** | **{total_issues}** | {'🚨 Action Needed' if total_issues > 0 else '✨ Clean UI'} |",
        "",
    ])

    # Error handling section if status is error
    if status == "error":
        err_msg = result.get("error", "Unknown error")
        err_type = result.get("error_type", "VisionError")
        lines.extend([
            "## ⚠️ Error Details",
            "",
            f"- **Error Type**: `{err_type}`",
            f"- **Message**: {err_msg}",
            "",
        ])
        return "\n".join(lines)

    # Detailed issues
    issues = result.get("issues", [])
    lines.append("## 🔍 Detailed Findings")
    lines.append("")

    if not issues:
        lines.extend([
            "> [!NOTE]",
            "> No visual regressions, layout breakages, or design discrepancies were found.",
            "> The audited UI meets all visual QA standards.",
            "",
        ])
    else:
        for idx, issue in enumerate(issues, 1):
            issue_id = issue.get("id") or f"ISS-{idx:02d}"
            severity = issue.get("severity", "minor").lower()
            badge = SEVERITY_BADGES.get(severity, f"[{severity.upper()}]")
            category = issue.get("category", "other").lower()
            cat_name = CATEGORY_NAMES.get(category, category)
            desc = issue.get("description", "").strip()
            bbox = issue.get("bbox")

            lines.append(f"### {idx}. `{issue_id}` {badge} — {cat_name}")
            lines.append("")
            lines.append(f"- **Description**: {desc}")

            if bbox and isinstance(bbox, list) and len(bbox) == 4:
                lines.append(f"- **Bounding Box [ymin, xmin, ymax, xmax]**: `{bbox}`")

            lines.append("")

    # Recommendations section
    lines.append("## 💡 Next Steps")
    lines.append("")
    if critical_cnt > 0 or major_cnt > 0:
        lines.append("- [ ] **Block deployment / merge** until Critical and Major issues are addressed.")
        lines.append("- [ ] Review CSS layout rules, flexbox/grid alignments, and container clipping boundaries.")
    elif minor_cnt > 0:
        lines.append("- [ ] Schedule minor alignment/spacing polish before release.")
    else:
        lines.append("- [x] All visual checks passed. Approved for UI merge.")

    lines.append("")
    return "\n".join(lines)


def format_json_report(result: Dict[str, Any], indent: int = 2) -> str:
    """
    Format audit result dictionary into a standardized, indented JSON string.
    """
    return json.dumps(result, indent=indent, ensure_ascii=False)


def print_report(
    result: Dict[str, Any],
    output_format: str = "markdown",
    stream: Optional[TextIO] = None,
) -> None:
    """
    Print the report in the requested format (markdown or json) to the given stream.
    """
    target_stream = stream if stream is not None else sys.stdout
    if output_format.lower() == "json":
        target_stream.write(format_json_report(result) + "\n")
    else:
        target_stream.write(format_markdown_report(result) + "\n")
    target_stream.flush()


# ---------------------------------------------------------------------------
# CLI Command
# ---------------------------------------------------------------------------

def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visual QA Report Generator using Gemini Flash Vision."
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to an image file (.png, .jpg, .webp, .gif) to audit OR a pre-existing audit JSON result file.",
    )
    parser.add_argument(
        "--compare",
        "-c",
        default=None,
        help="Optional path to reference design image (Figma mockup) for visual diff comparison.",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format: 'markdown' (default) or 'json'.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional output file path. If omitted, prints to standard output.",
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Exit with non-zero exit code (1) if critical or major visual issues are detected.",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini model name to use (default: gemini-2.5-flash).",
    )
    return parser.parse_args(args)


def main(args: Optional[List[str]] = None) -> int:
    ns = parse_args(args)
    input_path = Path(ns.input).expanduser().resolve()

    if not input_path.exists():
        print(f"[Error] Target input path does not exist: {input_path}", file=sys.stderr)
        return 2

    # Check if input is a JSON result file
    if input_path.suffix.lower() == ".json":
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except Exception as e:
            print(f"[Error] Failed to read JSON result from {input_path}: {e}", file=sys.stderr)
            return 2
    else:
        # Run live audit with GeminiFlashVisionClient
        client = GeminiFlashVisionClient(model=ns.model)
        result = client.audit_image(image_path=str(input_path), compare_image_path=ns.compare)

    # Format output
    if ns.format.lower() == "json":
        rendered = format_json_report(result)
    else:
        rendered = format_markdown_report(result)

    # Write output
    if ns.output:
        out_path = Path(ns.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"[Report] Successfully saved {ns.format} report to {out_path}", file=sys.stderr)
    else:
        print_report(result, output_format=ns.format)

    # Status check
    if result.get("status") == "error":
        return 3

    if ns.fail_on_issues:
        summary = result.get("summary", {})
        if summary.get("critical", 0) > 0 or summary.get("major", 0) > 0:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
