#!/usr/bin/env python3
"""
Unit tests for Gemini Flash Vision Client and Report Generator.
Runs fully isolated without requiring real network calls or API keys.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from vision_client import (
    GeminiFlashVisionClient,
    VisionApiError,
    VisionAuthError,
    VisionInputError,
    VisionParseError,
    resolve_gemini_api_key,
)
from vision_report import (
    format_json_report,
    format_markdown_report,
    main as report_main,
    print_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_image(tmp_path: Path) -> Path:
    """Create a minimal valid 1x1 PNG dummy image."""
    # Minimal 1x1 transparent PNG bytes
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
        b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    img_file = tmp_path / "screenshot.png"
    img_file.write_bytes(png_bytes)
    return img_file


@pytest.fixture
def temp_compare_image(tmp_path: Path) -> Path:
    """Create a minimal reference mockup image."""
    jpg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb"
    img_file = tmp_path / "mockup.jpg"
    img_file.write_bytes(jpg_bytes)
    return img_file


# ---------------------------------------------------------------------------
# Tests: Input Resolution & Validation
# ---------------------------------------------------------------------------

def test_resolve_image_valid(temp_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    data = client.resolve_and_validate_image(temp_image)

    assert data["path"] == str(temp_image.resolve())
    assert data["mime_type"] == "image/png"
    assert data["size_bytes"] > 0
    assert len(data["base64_data"]) > 0


def test_resolve_image_nonexistent() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    with pytest.raises(VisionInputError, match="does not exist"):
        client.resolve_and_validate_image("C:/nonexistent_folder/missing.png")


def test_resolve_image_empty_path() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    with pytest.raises(VisionInputError, match="cannot be empty"):
        client.resolve_and_validate_image("")


def test_resolve_image_unsupported_extension(tmp_path: Path) -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    txt_file = tmp_path / "invalid.txt"
    txt_file.write_text("not an image")

    with pytest.raises(VisionInputError, match="Unsupported image extension"):
        client.resolve_and_validate_image(txt_file)


def test_resolve_image_empty_file(tmp_path: Path) -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    empty_file = tmp_path / "empty.png"
    empty_file.write_bytes(b"")

    with pytest.raises(VisionInputError, match="empty \\(0 bytes\\)"):
        client.resolve_and_validate_image(empty_file)


def test_resolve_image_size_limit_exceeded(tmp_path: Path) -> None:
    client = GeminiFlashVisionClient(api_key="test-key", max_file_size_bytes=100)
    large_file = tmp_path / "large.png"
    large_file.write_bytes(b"a" * 200)

    with pytest.raises(VisionInputError, match="exceeds maximum allowed size"):
        client.resolve_and_validate_image(large_file)


# ---------------------------------------------------------------------------
# Tests: Prompt Construction
# ---------------------------------------------------------------------------

def test_build_audit_prompt_single_image() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    prompt = client.build_audit_prompt(compare_mode=False)

    assert "Layout Breakage / Overflow / Clipping" in prompt
    assert "Text Overlaps / Illegible Contrast" in prompt
    assert "Visual Alignment & Spacing Anomalies" in prompt
    assert "json" in prompt.lower()
    assert '"status": "ok" | "issues_found"' in prompt


def test_build_audit_prompt_compare_mode() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    prompt = client.build_audit_prompt(compare_mode=True)

    assert "TWO images" in prompt
    assert "Design vs Implementation Diffs" in prompt
    assert "reference design specification" in prompt.lower()


# ---------------------------------------------------------------------------
# Tests: Response Parsing & Normalization
# ---------------------------------------------------------------------------

def test_parse_response_clean_ok() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    raw = json.dumps({
        "status": "ok",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    })
    parsed = client.parse_response(raw)

    assert parsed["status"] == "ok"
    assert parsed["issues"] == []
    assert parsed["summary"]["critical"] == 0


def test_parse_response_with_markdown_fences() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    raw = """```json
{
  "status": "issues_found",
  "issues": [
    {
      "id": "ISS-01",
      "severity": "critical",
      "category": "layout_breakage",
      "description": "Submit button is clipped by card container boundary.",
      "bbox": [800, 200, 850, 400]
    },
    {
      "id": "ISS-02",
      "severity": "minor",
      "category": "text_overlap",
      "description": "Subtitle text is overlapping icon margin by 4px."
    }
  ],
  "summary": {
    "critical": 1,
    "major": 0,
    "minor": 1,
    "info": 0
  }
}
```"""
    parsed = client.parse_response(raw)

    assert parsed["status"] == "issues_found"
    assert len(parsed["issues"]) == 2
    assert parsed["issues"][0]["severity"] == "critical"
    assert parsed["issues"][0]["category"] == "layout_breakage"
    assert parsed["issues"][0]["bbox"] == [800, 200, 850, 400]
    assert parsed["summary"]["critical"] == 1
    assert parsed["summary"]["minor"] == 1


def test_parse_response_auto_correct_status_and_summary() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    # Model said "ok" but gave an issue, and summary counts were wrong
    raw = json.dumps({
        "status": "ok",
        "issues": [
            {
                "id": "ISS-99",
                "severity": "MAJOR",
                "category": "design_diff",
                "description": "Wrong brand color used on banner (#333 vs #0066FF).",
            }
        ],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    })
    parsed = client.parse_response(raw)

    assert parsed["status"] == "issues_found"
    assert parsed["summary"]["major"] == 1
    assert parsed["issues"][0]["severity"] == "major"
    assert parsed["issues"][0]["category"] == "design_diff"


def test_parse_response_invalid_json() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    with pytest.raises(VisionParseError, match="Failed to parse model output as JSON"):
        client.parse_response("Sorry, I cannot audit this image.")


def test_parse_response_empty() -> None:
    client = GeminiFlashVisionClient(api_key="test-key")
    with pytest.raises(VisionParseError, match="Empty response"):
        client.parse_response("")


# ---------------------------------------------------------------------------
# Tests: API Key Resolution
# ---------------------------------------------------------------------------

def test_resolve_gemini_api_key_explicit() -> None:
    assert resolve_gemini_api_key("my-explicit-key") == "my-explicit-key"


def test_resolve_gemini_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    assert resolve_gemini_api_key() == "env-gemini-key"


# ---------------------------------------------------------------------------
# Tests: Mock API Network & Error Handling
# ---------------------------------------------------------------------------

def _create_mock_gemini_http_response(candidate_text: str) -> MagicMock:
    """Helper to create a mocked urllib.request.urlopen context manager."""
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": candidate_text}],
                    "role": "model",
                }
            }
        ]
    }
    resp_bytes = json.dumps(payload).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_bytes
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def test_audit_image_rest_success(temp_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="valid-mock-key")

    mock_gemini_json = json.dumps({
        "status": "ok",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    })

    with patch("urllib.request.urlopen", return_value=_create_mock_gemini_http_response(mock_gemini_json)):
        result = client.audit_image(temp_image)

    assert result["status"] == "ok"
    assert result["issues"] == []
    assert result["image_path"] == str(temp_image.resolve())
    assert result["compare_image_path"] is None


def test_audit_image_rest_compare_images(temp_image: Path, temp_compare_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="valid-mock-key")

    mock_gemini_json = json.dumps({
        "status": "issues_found",
        "issues": [
            {
                "id": "DIFF-01",
                "severity": "major",
                "category": "design_diff",
                "description": "Header navbar padding is 16px instead of design spec 24px.",
            }
        ],
        "summary": {"critical": 0, "major": 1, "minor": 0, "info": 0},
    })

    with patch("urllib.request.urlopen", return_value=_create_mock_gemini_http_response(mock_gemini_json)) as mock_post:
        result = client.audit_image(temp_image, compare_image_path=temp_compare_image)

        # Verify that urllib was called with request containing both images
        req_arg = mock_post.call_args[0][0]
        body = json.loads(req_arg.data.decode("utf-8"))
        parts = body["contents"][0]["parts"]
        # 2 image parts + 1 text prompt part = 3 parts
        assert len(parts) == 3
        assert parts[0]["inlineData"]["mimeType"] == "image/png"
        assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"

    assert result["status"] == "issues_found"
    assert len(result["issues"]) == 1
    assert result["compare_image_path"] == str(temp_compare_image.resolve())


def test_audit_image_auth_error_raise(temp_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="")

    # Missing API key should raise VisionAuthError when raise_on_error=True
    with pytest.raises(VisionAuthError, match="GEMINI_API_KEY is not set"):
        client.audit_image(temp_image, raise_on_error=True)


def test_audit_image_auth_error_no_raise(temp_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="")

    result = client.audit_image(temp_image, raise_on_error=False)
    assert result["status"] == "error"
    assert result["error_type"] == "VisionAuthError"
    assert "GEMINI_API_KEY is not set" in result["error"]


def test_audit_image_http_403_error(temp_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="bad-key")

    http_err = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com/...",
        code=403,
        msg="Forbidden: API key not valid",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"error": {"message": "API key not valid"}}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        # With raise_on_error=True -> VisionAuthError
        with pytest.raises(VisionAuthError, match="HTTP 403"):
            client.audit_image(temp_image, raise_on_error=True)

        # With raise_on_error=False -> status: error
        res = client.audit_image(temp_image, raise_on_error=False)
        assert res["status"] == "error"
        assert res["error_type"] == "VisionAuthError"


def test_audit_image_http_500_error(temp_image: Path) -> None:
    client = GeminiFlashVisionClient(api_key="valid-key")

    http_err = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com/...",
        code=500,
        msg="Internal Server Error",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"error": {"message": "Backend crash"}}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(VisionApiError, match="HTTP 500"):
            client.audit_image(temp_image, raise_on_error=True)

        res = client.audit_image(temp_image, raise_on_error=False)
        assert res["status"] == "error"
        assert res["error_type"] == "VisionApiError"


# ---------------------------------------------------------------------------
# Tests: Report Generation & Formatting
# ---------------------------------------------------------------------------

def test_markdown_report_clean_pass() -> None:
    sample_result: Dict[str, Any] = {
        "status": "ok",
        "image_path": "C:/ui/button.png",
        "compare_image_path": None,
        "model": "gemini-2.5-flash",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    }

    md = format_markdown_report(sample_result)

    assert "# 📋 Visual QA Audit Report" in md
    assert "🟢 **PASS" in md
    assert "Total Issues" in md
    assert "No visual regressions" in md
    assert "Approved for UI merge" in md


def test_markdown_report_with_issues() -> None:
    sample_result: Dict[str, Any] = {
        "status": "issues_found",
        "image_path": "C:/ui/dashboard.png",
        "compare_image_path": "C:/ui/figma_mockup.png",
        "model": "gemini-2.5-flash",
        "issues": [
            {
                "id": "ISS-01",
                "severity": "critical",
                "category": "layout_breakage",
                "description": "Sidebar overflows main content container on 1024px viewport.",
                "bbox": [10, 0, 900, 250],
            },
            {
                "id": "ISS-02",
                "severity": "major",
                "category": "design_diff",
                "description": "Chart accent color is #FF0000 instead of Figma design #0088FE.",
            },
        ],
        "summary": {"critical": 1, "major": 1, "minor": 0, "info": 0},
    }

    md = format_markdown_report(sample_result)

    assert "🔴 **ACTION REQUIRED" in md
    assert "🔴 **[CRITICAL]**" in md
    assert "🟠 **[MAJOR]**" in md
    assert "Sidebar overflows main content" in md
    assert "**Bounding Box [ymin, xmin, ymax, xmax]**: `[10, 0, 900, 250]`" in md
    assert "Block deployment / merge" in md


def test_markdown_report_error_result() -> None:
    error_result: Dict[str, Any] = {
        "status": "error",
        "error": "Failed to connect to Gemini API endpoint.",
        "error_type": "VisionApiError",
        "image_path": "C:/ui/broken.png",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    }

    md = format_markdown_report(error_result)

    assert "⚠️ **AUDIT ERROR" in md
    assert "Failed to connect to Gemini API endpoint." in md
    assert "VisionApiError" in md


def test_json_report_formatting() -> None:
    sample_result: Dict[str, Any] = {
        "status": "ok",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    }

    json_str = format_json_report(sample_result)
    parsed = json.loads(json_str)
    assert parsed["status"] == "ok"
    assert parsed["summary"]["critical"] == 0


def test_print_report(capsys: pytest.CaptureFixture[str]) -> None:
    sample_result: Dict[str, Any] = {
        "status": "ok",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    }

    print_report(sample_result, output_format="markdown")
    captured = capsys.readouterr()
    assert "# 📋 Visual QA Audit Report" in captured.out

    print_report(sample_result, output_format="json")
    captured = capsys.readouterr()
    assert '"status": "ok"' in captured.out


# ---------------------------------------------------------------------------
# Tests: CLI Integration
# ---------------------------------------------------------------------------

def test_cli_report_from_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_path = tmp_path / "result.json"
    result_data = {
        "status": "issues_found",
        "image_path": "test.png",
        "issues": [
            {
                "id": "ISS-01",
                "severity": "critical",
                "category": "layout_breakage",
                "description": "Modal close button is cut off.",
            }
        ],
        "summary": {"critical": 1, "major": 0, "minor": 0, "info": 0},
    }
    json_path.write_text(json.dumps(result_data), encoding="utf-8")

    # Run CLI with format markdown
    code = report_main(["--input", str(json_path), "--format", "markdown"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Modal close button is cut off." in captured.out

    # Run CLI with --fail-on-issues
    code_fail = report_main(["--input", str(json_path), "--fail-on-issues"])
    assert code_fail == 1


def test_cli_save_to_file(tmp_path: Path) -> None:
    json_path = tmp_path / "result.json"
    result_data = {
        "status": "ok",
        "issues": [],
        "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
    }
    json_path.write_text(json.dumps(result_data), encoding="utf-8")

    out_file = tmp_path / "output_report.md"
    code = report_main(["--input", str(json_path), "--output", str(out_file)])
    assert code == 0
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "Visual QA Audit Report" in content
