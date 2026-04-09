"""Safety Scanner for user-editable prompt content.

Scans SOUL.md, PROJECT.md, and skill files before they are injected into the
static System Prompt.  Any match raises ``SafetyScanError`` so the caller can
abort prompt compilation rather than silently forwarding malicious content to
the LLM.

Threat model (§4.2 mvp_design.md)
-----------------------------------
- Prompt injection          — instructions embedded in config files that try to
                              hijack the agent's behaviour mid-session.
- System prompt override    — attempts to redefine the agent's identity or role.
- Rule bypass               — jailbreak phrases that instruct the model to ignore
                              its constraints.
- Data exfiltration         — patterns that try to leak memory or config to an
                              external endpoint.
- Invisible Unicode         — control / direction-override characters that hide
                              malicious content from human reviewers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


# ---------------------------------------------------------------------------
# Violation types
# ---------------------------------------------------------------------------


class ViolationKind(str, Enum):
    INVISIBLE_UNICODE = "invisible_unicode"
    PROMPT_INJECTION = "prompt_injection"
    SYSTEM_PROMPT_OVERRIDE = "system_prompt_override"
    RULE_BYPASS = "rule_bypass"
    DATA_EXFILTRATION = "data_exfiltration"


@dataclass(slots=True)
class ScanViolation:
    """A single finding produced by the scanner."""

    source: str          # human-readable label, e.g. "SOUL.md"
    kind: ViolationKind
    detail: str          # short description of what was found
    line: int | None = None  # 1-based line number, when applicable


class SafetyScanError(RuntimeError):
    """Raised when one or more violations are detected."""

    def __init__(self, violations: list[ScanViolation]) -> None:
        self.violations = violations
        summary = "; ".join(
            f"[{v.source}:{v.line or '?'}] {v.kind.value} — {v.detail}"
            for v in violations
        )
        super().__init__(f"Safety scan failed ({len(violations)} violation(s)): {summary}")


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# Invisible / dangerous Unicode code points.
# Includes: zero-width spaces, BOM, RTL/LTR overrides, bidirectional controls,
# soft hyphen, word joiner, etc.
_INVISIBLE_CODEPOINTS: frozenset[int] = frozenset([
    0x00AD,  # SOFT HYPHEN
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x200E,  # LEFT-TO-RIGHT MARK
    0x200F,  # RIGHT-TO-LEFT MARK
    0x202A,  # LEFT-TO-RIGHT EMBEDDING
    0x202B,  # RIGHT-TO-LEFT EMBEDDING
    0x202C,  # POP DIRECTIONAL FORMATTING
    0x202D,  # LEFT-TO-RIGHT OVERRIDE
    0x202E,  # RIGHT-TO-LEFT OVERRIDE  ← commonly used to hide text
    0x2060,  # WORD JOINER
    0x2061,  # FUNCTION APPLICATION
    0x2062,  # INVISIBLE TIMES
    0x2063,  # INVISIBLE SEPARATOR
    0x2064,  # INVISIBLE PLUS
    0x206A,  # INHIBIT SYMMETRIC SWAPPING
    0x206B,  # ACTIVATE SYMMETRIC SWAPPING
    0x206C,  # INHIBIT ARABIC FORM SHAPING
    0x206D,  # ACTIVATE ARABIC FORM SHAPING
    0x206E,  # NATIONAL DIGIT SHAPES
    0x206F,  # NOMINAL DIGIT SHAPES
    0xFEFF,  # BOM / ZERO WIDTH NO-BREAK SPACE
    0xFFF9,  # INTERLINEAR ANNOTATION ANCHOR
    0xFFFA,  # INTERLINEAR ANNOTATION SEPARATOR
    0xFFFB,  # INTERLINEAR ANNOTATION TERMINATOR
])

# Prompt injection — instructions that try to hijack the agent from within a
# config file.  Written as verbose patterns for readability.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignore\s+(previous|prior|above|all)\s+(instructions?|rules?|constraints?|prompts?)",
        r"disregard\s+(your\s+)?(previous|prior|above|all|system)?\s*(instructions?|rules?|constraints?|prompts?)",
        r"forget\s+(your\s+)?(previous|prior|above|all)?\s*(instructions?|rules?|constraints?)",
        r"override\s+(system\s+)?(prompt|instructions?|rules?)",
        r"new\s+instructions?:",
        r"updated?\s+instructions?:",
        r"system\s*:\s*you\s+(are|must|should|will)",
        r"<\s*/?system\s*>",          # XML-style system tag injection
        r"\[system\]",                 # bracket-style system tag
        r"###\s*system",               # markdown-style system heading
    ]
]

# System-prompt override — attempts to redefine the agent's identity.
_OVERRIDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"you\s+are\s+now\s+(?!a\s+(?:helpful|friendly|assistant))",  # "you are now X"
        r"from\s+now\s+on[\s,]+you\s+(are|must|should|will)",
        r"your\s+(new\s+)?(true\s+)?(role|identity|persona|name|purpose)\s+is",
        r"pretend\s+(you\s+are|to\s+be)",
        r"act\s+as\s+(if\s+you\s+(are|were)|a\s+different)",
        r"roleplay\s+as\s+(?!the\s+character)",  # allow fiction, block identity swap
        r"your\s+system\s+prompt\s+is",
        r"reveal\s+(your\s+)?(system\s+)?prompt",
        r"print\s+(your\s+)?(system\s+)?prompt",
        r"output\s+(your\s+)?(system\s+)?instructions?",
    ]
]

# Rule bypass — jailbreak vocabulary.
_BYPASS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bDAN\b",              # "Do Anything Now"
        r"\bjailbreak\b",
        r"\bbypass\s+(safety|filter|guard|restriction|rule|policy)",
        r"\bunrestricted\s+mode\b",
        r"\bgodmode\b",
        r"\bno\s+restrictions?\b",
        r"\bdo\s+anything\s+now\b",
        r"\bdev(eloper)?\s+mode\b",
    ]
]

# Data exfiltration — patterns that try to leak data to an external endpoint.
_EXFIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"send\s+(this|the|all|my|your|agent|memory|context|data)\s+(to|via)\s+https?://",
        r"post\s+.{0,60}\s+to\s+https?://",
        r"curl\s+.{0,120}",
        r"wget\s+.{0,120}",
        r"http(s)?://[^\s]{5,}\?.*?(secret|key|token|password|auth|session)",
        # base64-encoded blobs long enough to carry secrets
        r"(?:[A-Za-z0-9+/]{60,}={0,2})",
    ]
]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class SafetyScanner:
    """Scans text for injection, override, bypass, and exfiltration patterns.

    Usage::

        scanner = SafetyScanner()
        scanner.scan(soul_text, source="SOUL.md")          # raises on violation
        scanner.scan(project_ctx, source="PROJECT.md")
        scanner.scan_all({"SOUL.md": soul_text, "PROJECT.md": project_ctx})
    """

    def scan(self, text: str, source: str) -> None:
        """Scan *text* from *source*.  Raises ``SafetyScanError`` on findings."""
        violations = self._collect(text, source)
        if violations:
            raise SafetyScanError(violations)

    def scan_all(self, sources: dict[str, str]) -> None:
        """Scan multiple sources at once and raise with all findings combined."""
        all_violations: list[ScanViolation] = []
        for source, text in sources.items():
            all_violations.extend(self._collect(text, source))
        if all_violations:
            raise SafetyScanError(all_violations)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect(self, text: str, source: str) -> list[ScanViolation]:
        violations: list[ScanViolation] = []
        violations.extend(self._check_invisible_unicode(text, source))
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            violations.extend(self._check_line(line, lineno, source))
        return violations

    @staticmethod
    def _check_invisible_unicode(text: str, source: str) -> list[ScanViolation]:
        found: list[int] = []
        for ch in text:
            cp = ord(ch)
            if cp in _INVISIBLE_CODEPOINTS:
                found.append(cp)
        if not found:
            return []
        names = ", ".join(f"U+{cp:04X}" for cp in sorted(set(found)))
        return [ScanViolation(
            source=source,
            kind=ViolationKind.INVISIBLE_UNICODE,
            detail=f"invisible code points detected: {names}",
        )]

    @staticmethod
    def _check_line(line: str, lineno: int, source: str) -> list[ScanViolation]:
        violations: list[ScanViolation] = []

        for pat in _INJECTION_PATTERNS:
            m = pat.search(line)
            if m:
                violations.append(ScanViolation(
                    source=source,
                    kind=ViolationKind.PROMPT_INJECTION,
                    detail=f"matched pattern {pat.pattern!r}: {m.group(0)!r}",
                    line=lineno,
                ))

        for pat in _OVERRIDE_PATTERNS:
            m = pat.search(line)
            if m:
                violations.append(ScanViolation(
                    source=source,
                    kind=ViolationKind.SYSTEM_PROMPT_OVERRIDE,
                    detail=f"matched pattern {pat.pattern!r}: {m.group(0)!r}",
                    line=lineno,
                ))

        for pat in _BYPASS_PATTERNS:
            m = pat.search(line)
            if m:
                violations.append(ScanViolation(
                    source=source,
                    kind=ViolationKind.RULE_BYPASS,
                    detail=f"matched pattern {pat.pattern!r}: {m.group(0)!r}",
                    line=lineno,
                ))

        for pat in _EXFIL_PATTERNS:
            m = pat.search(line)
            if m:
                violations.append(ScanViolation(
                    source=source,
                    kind=ViolationKind.DATA_EXFILTRATION,
                    detail=f"matched pattern {pat.pattern!r}: {m.group(0)[:80]!r}",
                    line=lineno,
                ))

        return violations
