"""Shared publication-copy policy for the WeChat resource pipeline."""

from __future__ import annotations

from collections.abc import Iterable
import re


PUBLICATION_RISK_TERMS = (
    "人教版", "部编版", "清华附小", "万象思维", "高考快递", "小状元",
    "学而思", "新东方", "山东省", "广东省", "北京市", "上海市",
    "人教", "部编", "黄冈", "高途", "深圳", "学校", "名校",
)
PROMOTION_TERMS = (
    "waterinbullrun.com", "更多优质资源", "更多资源", "公众号", "推广",
)
WORDING_REPLACEMENTS = (("训练营", "资料"), ("课程", "资料"))

_REMOVAL_TERMS = tuple(sorted((*PUBLICATION_RISK_TERMS, *PROMOTION_TERMS), key=len, reverse=True))
_FORBIDDEN_TERMS = tuple(dict.fromkeys((*PUBLICATION_RISK_TERMS, *PROMOTION_TERMS, *(source for source, _ in WORDING_REPLACEMENTS))))
_EMPTY_BRACKETS_RE = re.compile(r"(?:【\s*】|《\s*》|（\s*）|\(\s*\)|\[\s*\])")
_PUNCTUATION_SPACING_RE = re.compile(r"\s*([，,。；;：:、！？!?])\s*")


def replace_publication_wording(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("publication copy must be a string")
    normalized = value
    for source, replacement in WORDING_REPLACEMENTS:
        normalized = normalized.replace(source, replacement)
    return re.sub(r"(?:资料\s*){2,}", "资料", normalized)


def sanitize_publication_copy(value: str) -> str:
    normalized = replace_publication_wording(value)
    for term in _REMOVAL_TERMS:
        normalized = re.sub(re.escape(term), " ", normalized, flags=re.IGNORECASE)
    normalized = _EMPTY_BRACKETS_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = _PUNCTUATION_SPACING_RE.sub(r"\1", normalized)
    normalized = re.sub(r"(?:资料\s*){2,}", "资料", normalized)
    return normalized.strip(" ，,。；;：:、！？!?")


def forbidden_publication_terms(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise TypeError("publication copy must be a string")
    folded = value.casefold()
    return tuple(term for term in _FORBIDDEN_TERMS if term.casefold() in folded)


def sanitize_visible_names(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("visible names must be an iterable of strings")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError("visible names must contain only strings")
        sanitized = sanitize_publication_copy(value)
        if sanitized and sanitized not in result:
            result.append(sanitized)
    return tuple(result)


def validate_publication_html(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("publication HTML must be a string")
    hits = forbidden_publication_terms(value)
    if hits:
        raise ValueError(f"forbidden publication term: {hits[0]}")
