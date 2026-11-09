#!/usr/bin/env python3
"""
AI Team Gate Contracts
Standard types, enums, dataclasses, and exit codes for multi-AI synergy and quality gates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Union


class ExitCode(IntEnum):
    """Deterministic exit codes for quality gates, CLI tools, and subagents."""
    PASS = 0
    GATE_FAIL = 1
    USAGE_ERROR = 2
    INPUT_ERROR = 3
    VISUAL_ISSUES = 4
    AUTH_CONFIG_ERROR = 5


# Convenience aliases matching ai_team_gate convention
EXIT_PASS = ExitCode.PASS
EXIT_GATE_FAIL = ExitCode.GATE_FAIL
EXIT_USAGE_ERROR = ExitCode.USAGE_ERROR
EXIT_INPUT_ERROR = ExitCode.INPUT_ERROR
EXIT_VISUAL_ISSUES = ExitCode.VISUAL_ISSUES
EXIT_AUTH_CONFIG_ERROR = ExitCode.AUTH_CONFIG_ERROR


class TaskType(str, Enum):
    """Supported task execution types."""
    EXTERNAL_REVIEW = "external_review"
    LOGIC = "logic"
    ARCHITECT = "architect"

    @classmethod
    def from_str(cls, val: Union[str, TaskType]) -> TaskType:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().lower()
        if norm in ("external_review", "review", "rev", "code_review"):
            return cls.EXTERNAL_REVIEW
        if norm in ("logic", "alg", "algorithm", "debug"):
            return cls.LOGIC
        if norm in ("architect", "arch", "architecture", "design"):
            return cls.ARCHITECT
        for member in cls:
            if member.value == norm or member.name.lower() == norm:
                return member
        raise ValueError(f"Unknown TaskType: '{val}'. Expected one of: {[m.value for m in cls]}")


class Severity(str, Enum):
    """Issue severity levels for audits and QA."""
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"

    @classmethod
    def from_str(cls, val: Union[str, Severity]) -> Severity:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().lower()
        for member in cls:
            if member.value == norm or member.name.lower() == norm:
                return member
        raise ValueError(f"Unknown Severity: '{val}'. Expected one of: {[m.value for m in cls]}")


class IssueCategory(str, Enum):
    """Categorization for UI / Visual QA and code issues."""
    LAYOUT_BREAKAGE = "layout_breakage"
    TEXT_OVERLAP = "text_overlap"
    DESIGN_DIFF = "design_diff"
    OTHER = "other"

    @classmethod
    def from_str(cls, val: Union[str, IssueCategory]) -> IssueCategory:
        if isinstance(val, cls):
            return val
        norm = str(val).strip().lower()
        for member in cls:
            if member.value == norm or member.name.lower() == norm:
                return member
        return cls.OTHER


@dataclass
class OptimizationResult:
    """Result of prompt optimization bridge."""
    original_prompt: str
    optimized_prompt: str
    task_type: TaskType
    original_tokens: int
    optimized_tokens: int
    token_reduction_pct: float
    is_bypassed: bool = False
    language: str = "en"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["task_type"] = self.task_type.value if hasattr(self.task_type, "value") else str(self.task_type)
        return data

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OptimizationResult:
        task_type_raw = data.get("task_type", TaskType.EXTERNAL_REVIEW)
        task_type = TaskType.from_str(task_type_raw) if not isinstance(task_type_raw, TaskType) else task_type_raw
        return cls(
            original_prompt=data.get("original_prompt", ""),
            optimized_prompt=data.get("optimized_prompt", ""),
            task_type=task_type,
            original_tokens=int(data.get("original_tokens", 0)),
            optimized_tokens=int(data.get("optimized_tokens", 0)),
            token_reduction_pct=float(data.get("token_reduction_pct", 0.0)),
            is_bypassed=bool(data.get("is_bypassed", False)),
            language=data.get("language", "en"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class VisionQAIssue:
    """Represents a specific visual or layout defect detected by Vision QA."""
    category: IssueCategory
    severity: Severity
    description: str
    selector: Optional[str] = None
    location: Optional[str] = None
    screenshot_path: Optional[str] = None
    suggestion: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value if hasattr(self.category, "value") else str(self.category)
        data["severity"] = self.severity.value if hasattr(self.severity, "value") else str(self.severity)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VisionQAIssue:
        cat_raw = data.get("category", IssueCategory.OTHER)
        sev_raw = data.get("severity", Severity.INFO)
        return cls(
            category=IssueCategory.from_str(cat_raw),
            severity=Severity.from_str(sev_raw),
            description=data.get("description", ""),
            selector=data.get("selector"),
            location=data.get("location"),
            screenshot_path=data.get("screenshot_path"),
            suggestion=data.get("suggestion"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class VisionQAReport:
    """Summary report of Vision QA verification."""
    passed: bool
    issues: List[VisionQAIssue] = field(default_factory=list)
    summary: str = ""
    total_critical: int = 0
    total_major: int = 0
    total_minor: int = 0
    exit_code: ExitCode = ExitCode.PASS
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_issue(self, issue: VisionQAIssue) -> None:
        """Add an issue and update internal counts and pass status."""
        self.issues.append(issue)
        if issue.severity == Severity.CRITICAL:
            self.total_critical += 1
            self.passed = False
            self.exit_code = ExitCode.VISUAL_ISSUES
        elif issue.severity == Severity.MAJOR:
            self.total_major += 1
            self.passed = False
            self.exit_code = ExitCode.VISUAL_ISSUES
        elif issue.severity == Severity.MINOR:
            self.total_minor += 1

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["exit_code"] = int(self.exit_code)
        data["issues"] = [issue.to_dict() if hasattr(issue, "to_dict") else asdict(issue) for issue in self.issues]
        return data

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VisionQAReport:
        issues_raw = data.get("issues", [])
        issues = [VisionQAIssue.from_dict(i) if isinstance(i, dict) else i for i in issues_raw]
        exit_code_raw = data.get("exit_code", ExitCode.PASS)
        try:
            exit_code = ExitCode(exit_code_raw)
        except ValueError:
            exit_code = ExitCode.PASS
        return cls(
            passed=bool(data.get("passed", True)),
            issues=issues,
            summary=data.get("summary", ""),
            total_critical=int(data.get("total_critical", 0)),
            total_major=int(data.get("total_major", 0)),
            total_minor=int(data.get("total_minor", 0)),
            exit_code=exit_code,
            metadata=dict(data.get("metadata", {})),
        )


__all__ = [
    "ExitCode",
    "EXIT_PASS",
    "EXIT_GATE_FAIL",
    "EXIT_USAGE_ERROR",
    "EXIT_INPUT_ERROR",
    "EXIT_VISUAL_ISSUES",
    "EXIT_AUTH_CONFIG_ERROR",
    "TaskType",
    "Severity",
    "IssueCategory",
    "OptimizationResult",
    "VisionQAIssue",
    "VisionQAReport",
]
