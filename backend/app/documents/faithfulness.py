from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from app.documents.ir import Document, DocumentBlock


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])[+-]?(?:\d+(?:[.,]\d+)*|\.\d+)%?")
IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.:+/#-])[A-Za-z][A-Za-z0-9_.:+/#-]{2,}"
    r"(?![A-Za-z0-9_.:+/#-])"
)
IDENTIFIER_SIGNAL_PATTERN = re.compile(r"[A-Z0-9_.:+/#-]")
CODE_SPAN_PATTERN = re.compile(r"`{1,3}[^`]+`{1,3}", re.DOTALL)
FORMULA_SYMBOLS = set("=+-*/^_∑∫√≈≠≤≥<>[]{}()")


@dataclass(frozen=True, slots=True)
class FaithfulnessIssue:
    code: str
    message: str
    block_id: str | None = None


@dataclass(frozen=True, slots=True)
class FaithfulnessReport:
    accepted: bool
    issues: tuple[FaithfulnessIssue, ...] = ()
    metrics: dict[str, float | int] = field(default_factory=dict)


def validate_block_edit(
    block: DocumentBlock,
    *,
    returned_source_ids: Iterable[str],
    minimum_length_ratio: float = 0.72,
    formula_symbol_ratio: float = 0.8,
) -> FaithfulnessReport:
    original = block.original_text
    edited = block.display_text
    issues: list[FaithfulnessIssue] = []
    expected_sources = [item.source_id for item in block.source_references]
    actual_sources = list(returned_source_ids)
    if actual_sources != expected_sources:
        issues.append(
            FaithfulnessIssue(
                "SOURCE_IDS_CHANGED",
                "整理响应没有按原顺序完整保留 source_id",
                block.block_id,
            )
        )
    original_length = len(_content_characters(original))
    edited_length = len(_content_characters(edited))
    length_ratio = edited_length / max(1, original_length)
    if original_length and length_ratio < minimum_length_ratio:
        issues.append(
            FaithfulnessIssue(
                "TEXT_ABNORMALLY_SHORTENED",
                f"整理后文本长度比例 {length_ratio:.3f} 低于阈值",
                block.block_id,
            )
        )
    missing_numbers = _missing_tokens(
        NUMBER_PATTERN.findall(original), NUMBER_PATTERN.findall(edited)
    )
    if missing_numbers:
        issues.append(
            FaithfulnessIssue(
                "NUMBERS_LOST",
                f"数字缺失：{', '.join(missing_numbers[:8])}",
                block.block_id,
            )
        )
    identifiers = _technical_identifiers(original)
    missing_identifiers = _missing_tokens(
        (token.rstrip(".:") for token in identifiers),
        (token.rstrip(".:") for token in IDENTIFIER_PATTERN.findall(edited)),
        casefold=True,
    )
    if missing_identifiers:
        issues.append(
            FaithfulnessIssue(
                "IDENTIFIERS_LOST",
                f"英文专名或标识符缺失：{', '.join(missing_identifiers[:8])}",
                block.block_id,
            )
        )
    missing_code = [
        item for item in CODE_SPAN_PATTERN.findall(original) if item not in edited
    ]
    if missing_code:
        issues.append(
            FaithfulnessIssue(
                "CODE_DAMAGED",
                "代码片段没有被逐字保留",
                block.block_id,
            )
        )
    original_formula_count = sum(original.count(symbol) for symbol in FORMULA_SYMBOLS)
    edited_formula_count = sum(edited.count(symbol) for symbol in FORMULA_SYMBOLS)
    actual_formula_ratio = edited_formula_count / max(1, original_formula_count)
    if original_formula_count and actual_formula_ratio < formula_symbol_ratio:
        issues.append(
            FaithfulnessIssue(
                "FORMULA_SYMBOLS_LOST",
                f"公式符号保留比例 {actual_formula_ratio:.3f} 低于阈值",
                block.block_id,
            )
        )
    return FaithfulnessReport(
        accepted=not issues,
        issues=tuple(issues),
        metrics={
            "original_characters": original_length,
            "edited_characters": edited_length,
            "length_ratio": round(length_ratio, 6),
            "original_formula_symbols": original_formula_count,
            "edited_formula_symbols": edited_formula_count,
        },
    )


def validate_document_faithfulness(
    document: Document,
    *,
    minimum_length_ratio: float = 0.72,
    formula_symbol_ratio: float = 0.8,
) -> FaithfulnessReport:
    issues: list[FaithfulnessIssue] = []
    original_characters = 0
    edited_characters = 0
    previous_time = -1.0
    for block in document.all_blocks():
        start = block.timestamp.start_seconds
        if start is not None:
            if start + 1e-6 < previous_time:
                issues.append(
                    FaithfulnessIssue(
                        "TIME_ORDER_CHANGED",
                        "文档块时间顺序发生变化",
                        block.block_id,
                    )
                )
            previous_time = start
        if block.block_type == "image":
            continue
        report = validate_block_edit(
            block,
            returned_source_ids=[
                reference.source_id for reference in block.source_references
            ],
            minimum_length_ratio=minimum_length_ratio,
            formula_symbol_ratio=formula_symbol_ratio,
        )
        issues.extend(report.issues)
        original_characters += int(report.metrics["original_characters"])
        edited_characters += int(report.metrics["edited_characters"])
    ratio = edited_characters / max(1, original_characters)
    return FaithfulnessReport(
        accepted=not issues,
        issues=tuple(issues),
        metrics={
            "block_count": len(document.all_blocks()),
            "source_reference_count": len(document.source_ids()),
            "original_characters": original_characters,
            "edited_characters": edited_characters,
            "length_ratio": round(ratio, 6),
        },
    )


def _content_characters(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _missing_tokens(
    tokens: Iterable[str],
    observed_tokens: Iterable[str],
    *,
    casefold: bool = False,
) -> list[str]:
    required = Counter(item.casefold() if casefold else item for item in tokens)
    observed = Counter(item.casefold() if casefold else item for item in observed_tokens)
    return list((required - observed).elements())


def _technical_identifiers(text: str) -> list[str]:
    """Return tokens whose spelling carries an identifier-like signal.

    Plain lowercase English words in an ASR transcript can be lyrics, filler,
    or speech recognition noise. Treating every three-letter word as a proper
    name made harmless cleanup such as removing ``uuuuu yeah`` fail the whole
    article. Mixed-case names, acronyms, versions, model names, and tokens with
    identifier punctuation remain protected.
    """

    return [
        token
        for token in IDENTIFIER_PATTERN.findall(text)
        if IDENTIFIER_SIGNAL_PATTERN.search(token)
    ]
