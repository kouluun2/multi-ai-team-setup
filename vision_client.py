#!/usr/bin/env python3
"""
Gemini Flash Vision Client for UI / Visual QA Feedback Loop.
Provides deterministic visual audit of UI screenshots and design diffs.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Prevent Unicode/Emoji encoding errors on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Optional google.genai SDK integration
try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VisionError(Exception):
    """Base exception for all Vision QA client errors."""
    pass


class VisionInputError(VisionError):
    """Raised when an input image file is invalid, missing, or exceeds size limits."""
    pass


class VisionAuthError(VisionError):
    """Raised when the Gemini API key is missing, unauthorized, or rejected."""
    pass


class VisionApiError(VisionError):
    """Raised when the Gemini API returns an HTTP error, network error, or timeout."""
    pass


class VisionParseError(VisionError):
    """Raised when the model response cannot be parsed into the required schema."""
    pass


# ---------------------------------------------------------------------------
# Key Resolution Helpers
# ---------------------------------------------------------------------------

def resolve_gemini_api_key(explicit_key: Optional[str] = None) -> str:
    """
    Resolve Gemini API key from explicit arg, environment variables,
    or Windows User Registry (HKEY_CURRENT_USER\\Environment).
    """
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()

    # 1. Environment variables
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val

    # 2. Windows registry check
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                    try:
                        val, _ = winreg.QueryValueEx(reg_key, var)
                        if val and str(val).strip():
                            return str(val).strip()
                    except FileNotFoundError:
                        continue
        except Exception:
            pass

    return ""


# ---------------------------------------------------------------------------
# GeminiFlashVisionClient
# ---------------------------------------------------------------------------

class GeminiFlashVisionClient:
    """
    Client for automating Visual QA audits of UI screenshots using Gemini Flash Vision.
    Supports single-image audits and design vs implementation comparison.
    """

    ALLOWED_EXTENSIONS: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    MAX_FILE_SIZE_BYTES: int = 20 * 1024 * 1024  # 20 MB

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        timeout: int = 60,
        backend: str = "auto",
        max_file_size_bytes: int = MAX_FILE_SIZE_BYTES,
    ) -> None:
        """
        Initialize the vision QA client.

        :param api_key: Gemini API key. If omitted, resolved from env / registry.
        :param model: Gemini model name (default: gemini-2.5-flash).
        :param timeout: Network timeout in seconds.
        :param backend: "auto", "rest", or "sdk".
        :param max_file_size_bytes: Maximum allowed image file size in bytes (default 20MB).
        """
        self.api_key = resolve_gemini_api_key(api_key)
        self.model = model
        self.timeout = timeout
        self.backend = backend
        self.max_file_size_bytes = max_file_size_bytes

    # -----------------------------------------------------------------------
    # Image resolution and validation
    # -----------------------------------------------------------------------

    def resolve_and_validate_image(self, image_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Safely validate an image file, verify path existence, extension, and file size.
        Returns a dictionary with canonical path, mime_type, bytes, and base64 string.
        """
        if not image_path:
            raise VisionInputError("Image path cannot be empty.")

        path_obj = Path(image_path).expanduser().resolve()
        if not path_obj.exists():
            raise VisionInputError(f"Image file does not exist: {path_obj}")
        if not path_obj.is_file():
            raise VisionInputError(f"Target is not a valid file: {path_obj}")

        # Extension check
        ext = path_obj.suffix.lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            allowed = ", ".join(self.ALLOWED_EXTENSIONS)
            raise VisionInputError(
                f"Unsupported image extension '{ext}'. Allowed extensions: {allowed}"
            )

        # File size check
        try:
            size = path_obj.stat().st_size
        except OSError as e:
            raise VisionInputError(f"Unable to read file metadata for {path_obj}: {e}") from e

        if size == 0:
            raise VisionInputError(f"Image file is empty (0 bytes): {path_obj}")
        if size > self.max_file_size_bytes:
            max_mb = self.max_file_size_bytes / (1024 * 1024)
            actual_mb = size / (1024 * 1024)
            raise VisionInputError(
                f"Image file exceeds maximum allowed size ({actual_mb:.2f}MB > {max_mb:.1f}MB): {path_obj}"
            )

        # Mime type detection
        mime_type, _ = mimetypes.guess_type(str(path_obj))
        if not mime_type or not mime_type.startswith("image/"):
            ext_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }
            mime_type = ext_map.get(ext, "image/png")

        # Read bytes and encode to base64
        try:
            raw_bytes = path_obj.read_bytes()
        except OSError as e:
            raise VisionInputError(f"Failed to read image bytes from {path_obj}: {e}") from e

        b64_data = base64.b64encode(raw_bytes).decode("ascii")

        return {
            "path": str(path_obj),
            "filename": path_obj.name,
            "mime_type": mime_type,
            "size_bytes": size,
            "raw_bytes": raw_bytes,
            "base64_data": b64_data,
        }

    # -----------------------------------------------------------------------
    # Prompt construction
    # -----------------------------------------------------------------------

    def build_audit_prompt(self, compare_mode: bool = False) -> str:
        """
        Construct structured prompt instructing Gemini Flash to audit UI quality.
        """
        if compare_mode:
            task_intro = (
                "You are an expert Senior UI/UX QA and Frontend Engineer performing a rigorous visual audit.\n"
                "You are provided with TWO images:\n"
                "1. Image 1: The actual UI implementation screenshot.\n"
                "2. Image 2: The reference design specification (e.g. Figma design / mockup).\n\n"
                "Compare the implementation against the reference design and evaluate the implementation for visual flaws."
            )
            diff_clause = (
                "4. Design vs Implementation Diffs:\n"
                "   - Missing, misplaced, or unexpected UI elements compared to reference design.\n"
                "   - Font, color, corner radius, padding, or sizing discrepancies.\n"
                "   - Icon, badge, or asset mismatches."
            )
        else:
            task_intro = (
                "You are an expert Senior UI/UX QA and Frontend Engineer performing a rigorous visual audit\n"
                "of the provided UI implementation screenshot."
            )
            diff_clause = (
                "4. Visual Consistency & Polish:\n"
                "   - Visual hierarchy irregularities, inconsistent padding, broken borders, or styling artifacts."
            )

        prompt = f"""{task_intro}

Carefully analyze the image(s) for the following defect categories:
1. Layout Breakage / Overflow / Clipping:
   - Elements overflowing container boundaries or viewport.
   - Truncated text with missing ellipsis or clipped UI components.
   - Broken grids, unintended wrapping, or distorted aspect ratios.
2. Text Overlaps / Illegible Contrast:
   - Overlapping text layers or text overlapping images/icons.
   - WCAG contrast failures, illegible font weights, or unreadable labels.
3. Visual Alignment & Spacing Anomalies:
   - Misaligned margins, unequal gaps in lists/grids, or uneven vertical/horizontal centering.
   - Inconsistent padding or baseline alignment.
{diff_clause}

Output Requirements:
You MUST respond with a single, valid JSON object strictly conforming to this schema:
{{
  "status": "ok" | "issues_found",
  "issues": [
    {{
      "id": "ISS-01",
      "severity": "critical" | "major" | "minor" | "info",
      "category": "layout_breakage" | "text_overlap" | "design_diff" | "other",
      "description": "<concise explanation of the defect and recommended fix>",
      "bbox": [ymin, xmin, ymax, xmax]
    }}
  ],
  "summary": {{
    "critical": <count>,
    "major": <count>,
    "minor": <count>,
    "info": <count>
  }}
}}

Rules:
- If NO visual defects or design diffs are present, set "status" to "ok", "issues" to [], and all summary counts to 0.
- If one or more defects are present, set "status" to "issues_found", list each defect in "issues", and provide accurate counts in "summary".
- "severity" must be strictly one of: "critical", "major", "minor", "info".
- "category" must be strictly one of: "layout_breakage", "text_overlap", "design_diff", "other".
- "bbox" is optional; if provided, use normalized coordinates [ymin, xmin, ymax, xmax] in the 0-1000 range.
- Return ONLY the JSON object. Do not wrap in markdown or prose outside the JSON.
"""
        return prompt

    # -----------------------------------------------------------------------
    # Response Parsing & Normalization
    # -----------------------------------------------------------------------

    def parse_response(self, raw_text: str) -> Dict[str, Any]:
        """
        Parse and sanitize the raw JSON response from Gemini Flash.
        Guarantees strict schema adherence: status, issues, and summary counts.
        """
        if not raw_text or not raw_text.strip():
            raise VisionParseError("Empty response received from vision model.")

        text = raw_text.strip()

        # Remove markdown code fences if present (e.g. ```json ... ```)
        code_fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if code_fence_match:
            text = code_fence_match.group(1).strip()
        else:
            # Fallback: find outermost curly braces
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                text = text[start_idx : end_idx + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise VisionParseError(f"Failed to parse model output as JSON: {e}\nRaw output:\n{raw_text[:500]}") from e

        if not isinstance(data, dict):
            raise VisionParseError(f"Model output root must be a JSON object, got {type(data).__name__}")

        # Validate and sanitize issues
        raw_issues = data.get("issues", [])
        if not isinstance(raw_issues, list):
            raw_issues = []

        valid_severities = {"critical", "major", "minor", "info"}
        valid_categories = {"layout_breakage", "text_overlap", "design_diff", "other"}

        sanitized_issues: List[Dict[str, Any]] = []
        summary_counts = {"critical": 0, "major": 0, "minor": 0, "info": 0}

        for idx, item in enumerate(raw_issues, 1):
            if not isinstance(item, dict):
                continue

            issue_id = str(item.get("id") or f"ISS-{idx:02d}").strip()
            severity = str(item.get("severity", "minor")).strip().lower()
            if severity not in valid_severities:
                severity = "minor"

            category = str(item.get("category", "other")).strip().lower()
            if category not in valid_categories:
                category = "other"

            description = str(item.get("description", "")).strip() or "Unspecified visual issue detected."

            sanitized_issue: Dict[str, Any] = {
                "id": issue_id,
                "severity": severity,
                "category": category,
                "description": description,
            }

            bbox = item.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                try:
                    sanitized_issue["bbox"] = [int(v) for v in bbox]
                except (ValueError, TypeError):
                    pass

            sanitized_issues.append(sanitized_issue)
            summary_counts[severity] += 1

        # Determine status
        raw_status = str(data.get("status", "")).strip().lower()
        if sanitized_issues:
            status = "issues_found"
        elif raw_status == "ok" or not sanitized_issues:
            status = "ok"
        else:
            status = "ok"

        return {
            "status": status,
            "issues": sanitized_issues,
            "summary": summary_counts,
        }

    # -----------------------------------------------------------------------
    # API Invocation: REST Backend
    # -----------------------------------------------------------------------

    def _call_rest_api(
        self,
        image_data: Dict[str, Any],
        compare_data: Optional[Dict[str, Any]],
        prompt: str,
    ) -> str:
        """
        Execute call to Gemini generateContent REST endpoint using standard urllib.
        """
        if not self.api_key:
            raise VisionAuthError("GEMINI_API_KEY is not set. Provide an API key via argument, env, or registry.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        parts: List[Dict[str, Any]] = [
            {
                "inlineData": {
                    "mimeType": image_data["mime_type"],
                    "data": image_data["base64_data"],
                }
            }
        ]

        if compare_data:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": compare_data["mime_type"],
                        "data": compare_data["base64_data"],
                    }
                }
            )

        parts.append({"text": prompt})

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }

        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=req_body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_bytes = resp.read()
                resp_json = json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass

            if e.code in (401, 403):
                raise VisionAuthError(f"Gemini API authentication failed (HTTP {e.code}): {error_body or e.reason}") from e
            raise VisionApiError(f"Gemini API returned HTTP {e.code}: {error_body or e.reason}") from e
        except urllib.error.URLError as e:
            raise VisionApiError(f"Network error connecting to Gemini API: {e.reason}") from e
        except TimeoutError as e:
            raise VisionApiError(f"Gemini API request timed out after {self.timeout}s: {e}") from e
        except Exception as e:
            raise VisionApiError(f"Unexpected error calling Gemini API: {e}") from e

        # Extract generated text from response
        try:
            candidates = resp_json.get("candidates", [])
            if not candidates:
                raise VisionParseError(f"Gemini API returned no candidates: {resp_json}")
            first_candidate = candidates[0]
            parts_resp = first_candidate.get("content", {}).get("parts", [])
            if not parts_resp:
                raise VisionParseError("Gemini API candidate has no content parts")
            text = "".join(part.get("text", "") for part in parts_resp)
            return text
        except (KeyError, IndexError, TypeError) as e:
            raise VisionParseError(f"Unexpected structure in Gemini API response: {e}") from e

    # -----------------------------------------------------------------------
    # API Invocation: SDK Backend
    # -----------------------------------------------------------------------

    def _call_sdk_api(
        self,
        image_data: Dict[str, Any],
        compare_data: Optional[Dict[str, Any]],
        prompt: str,
    ) -> str:
        """
        Execute call using the official google.genai SDK.
        """
        if not HAS_GENAI_SDK or genai is None or genai_types is None:
            raise VisionApiError("google-genai SDK is not installed or unavailable.")

        if not self.api_key:
            raise VisionAuthError("GEMINI_API_KEY is not set. Provide an API key via argument, env, or registry.")

        try:
            client = genai.Client(api_key=self.api_key)
            contents: List[Any] = [
                genai_types.Part.from_bytes(
                    data=image_data["raw_bytes"],
                    mime_type=image_data["mime_type"],
                )
            ]
            if compare_data:
                contents.append(
                    genai_types.Part.from_bytes(
                        data=compare_data["raw_bytes"],
                        mime_type=compare_data["mime_type"],
                    )
                )
            contents.append(prompt)

            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            return response.text or ""
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "403" in err_str or "API_KEY" in err_str.upper():
                raise VisionAuthError(f"Gemini SDK authentication error: {e}") from e
            raise VisionApiError(f"Gemini SDK invocation error: {e}") from e

    # -----------------------------------------------------------------------
    # High-level Audit Method
    # -----------------------------------------------------------------------

    def audit_image(
        self,
        image_path: Union[str, Path],
        compare_image_path: Optional[Union[str, Path]] = None,
        raise_on_error: bool = False,
    ) -> Dict[str, Any]:
        """
        Perform a visual QA audit on the given image, optionally comparing against a reference image.

        :param image_path: Path to the implementation screenshot.
        :param compare_image_path: Optional path to the reference design mockup.
        :param raise_on_error: If True, raises VisionError exceptions; if False, returns error dict.
        :return: Structured result dictionary.
        """
        try:
            image_data = self.resolve_and_validate_image(image_path)
            compare_data = (
                self.resolve_and_validate_image(compare_image_path)
                if compare_image_path
                else None
            )

            prompt = self.build_audit_prompt(compare_mode=bool(compare_data))

            # Select backend
            backend_to_use = self.backend.lower()
            if backend_to_use == "auto":
                # Default to REST for maximum determinism and consistency
                backend_to_use = "rest"

            if backend_to_use == "sdk":
                raw_text = self._call_sdk_api(image_data, compare_data, prompt)
            else:
                raw_text = self._call_rest_api(image_data, compare_data, prompt)

            result = self.parse_response(raw_text)
            result["image_path"] = image_data["path"]
            result["compare_image_path"] = compare_data["path"] if compare_data else None
            result["model"] = self.model
            return result

        except VisionError as e:
            if raise_on_error:
                raise
            return {
                "status": "error",
                "error": str(e),
                "error_type": e.__class__.__name__,
                "issues": [],
                "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
                "image_path": str(image_path) if image_path else None,
                "compare_image_path": str(compare_image_path) if compare_image_path else None,
                "model": self.model,
            }
        except Exception as e:
            if raise_on_error:
                raise VisionApiError(f"Unexpected error during visual audit: {e}") from e
            return {
                "status": "error",
                "error": str(e),
                "error_type": "UnexpectedError",
                "issues": [],
                "summary": {"critical": 0, "major": 0, "minor": 0, "info": 0},
                "image_path": str(image_path) if image_path else None,
                "compare_image_path": str(compare_image_path) if compare_image_path else None,
                "model": self.model,
            }
