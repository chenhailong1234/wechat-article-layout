from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
import re
from types import MappingProxyType
from unicodedata import category

from .wechat_resource_pipeline.policy import (
    PUBLICATION_RISK_TERMS,
    replace_publication_wording,
)


MIN_TITLE_LENGTH = 30
MAX_TITLE_LENGTH = 60
_MAX_FACTUAL_CANDIDATE_PLANS = 72
_REJECTED_TITLE_CATEGORIES = frozenset(("Cc", "Cf", "Cs", "Zl", "Zp"))
_CHINESE_GRADE_DIGITS = "零一二三四五六七八九"
_RANGE_GRADE_TERMS = tuple(
    f"{_CHINESE_GRADE_DIGITS[start]}至{_CHINESE_GRADE_DIGITS[end]}年级"
    for start in range(1, 10)
    for end in range(start + 1, 10)
)
_HIGH_RANGE_TERMS = tuple(
    f"高{start}至高{end}"
    for start in "一二三"
    for end in "一二三"
    if "一二三".index(start) < "一二三".index(end)
)

GRADE_TERMS = (
    "高一高二高三",
    "一至六年级",
    "一至五年级",
    "七至九年级",
    "一年级",
    "二年级",
    "三年级",
    "四年级",
    "五年级",
    "六年级",
    "七年级",
    "八年级",
    "九年级",
    "高一",
    "高二",
    "高三",
    "小学",
    "初中",
    "高中",
    *_RANGE_GRADE_TERMS,
    *_HIGH_RANGE_TERMS,
)
SUBJECT_TERMS = (
    "语文",
    "数学",
    "英语",
    "物理",
    "化学",
    "生物",
    "历史",
    "地理",
    "政治",
    "科学",
    "全科",
)
SCENARIO_MAP = MappingProxyType(
    {
        "期末专项复习": "期末专项复习", "期中专项复习": "期中专项复习",
        "暑假预习": "暑假预习", "寒假预习": "寒假预习",
        "开学衔接": "开学衔接", "一轮复习": "一轮复习", "二轮复习": "二轮复习",
        "同步学习": "同步学习", "期末": "期末", "期中": "期中", "暑假": "暑假",
        "寒假": "寒假", "开学": "开学", "一轮": "一轮", "二轮": "二轮", "同步": "同步",
    }
)
FORMAT_MAP = MappingProxyType(
    {
        "可打印练习试卷": "可打印练习试卷",
        "可打印试卷": "可打印试卷",
        "练习试卷": "练习试卷", "试卷": "试卷",
        "专项练习": "专项练习",
        "专题讲义": "专题讲义", "讲义": "讲义",
        "视频课程": "视频课程", "视频": "视频",
        "知识清单": "知识清单",
        "练习": "练习",
        "学习资料包": "学习资料包", "资料包": "资料包",
    }
)
BENEFIT_MAP = MappingProxyType(
    {
        "可打印文档": "可打印文档", "配套音频": "配套音频",
        "答案解析": "答案解析",
        "含答案": "含答案",
        "解析": "解析", "可打印": "可打印", "音频": "音频",
    }
)
_FORMAT_COVERED_BENEFITS = MappingProxyType(
    {
        "可打印": ("可打印试卷", "可打印练习试卷"),
    }
)
# Backward-compatible export for existing callers.
RISK_TERMS = PUBLICATION_RISK_TERMS
_QUANTITY_RE = re.compile(r"(?:全)?(\d+)(题|讲|节|专题|套|册|词|科)")
_NUMERIC_SINGLE_GRADES = {
    "1": "一年级", "2": "二年级", "3": "三年级", "4": "四年级",
    "5": "五年级", "6": "六年级", "7": "七年级", "8": "八年级", "9": "九年级",
}
_CHINESE_NUMBER_TOKEN = r"[零〇一二三四五六七八九十百千万亿兆京垓秭穰沟涧正载两壹贰叁肆伍陆柒捌玖拾佰仟萬億]+"
_GRADE_RANGE_CONNECTORS = frozenset("到至-—–~～")
_GRADE_COMPOSITE_CONNECTORS = _GRADE_RANGE_CONNECTORS
_CHINESE_K12_VALUES = {character: index for index, character in enumerate(_CHINESE_GRADE_DIGITS) if index}
_CHINESE_GRADE_NUMBER_CHARACTERS = frozenset("零〇一二三四五六七八九十百千万亿兆京垓秭穰沟涧正载两壹贰叁肆伍陆柒捌玖拾佰仟萬億")
_QUANTITY_UNITS = frozenset("题讲节套册词科")
_QUANTITY_TERMS = ("专题", *_QUANTITY_UNITS)
_CHINESE_QUANTITY_RE = re.compile(
    rf"{_CHINESE_NUMBER_TOKEN}(?:专题|[题讲节套册词科])"
)
_RANGE_PUNCTUATION = frozenset("，、。；;：:（）()《》【】|｜·")
_GRADE_LIST_SEPARATORS = frozenset("、,，/")
_NUMBER_LABEL_SEPARATORS = frozenset("-—–/，,、")


def _tuple_of_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{field_name} must be a list or tuple of strings")
    if any(not isinstance(member, str) for member in value):
        raise TypeError(f"{field_name} must contain only strings")
    return tuple(value)


class TitlePolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TitleFacts:
    source_texts: tuple[str, ...]
    grades: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    scenarios: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    quantities: tuple[str, ...] = ()
    benefits: tuple[str, ...] = ()
    blocked_terms: tuple[str, ...] = ()
    risk_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "source_texts",
            "grades",
            "subjects",
            "scenarios",
            "formats",
            "quantities",
            "benefits",
            "blocked_terms",
            "risk_terms",
        ):
            object.__setattr__(
                self,
                field_name,
                _tuple_of_strings(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True)
class _GradeListScan:
    end: int
    ambiguous: bool
    grades: tuple[str, ...] = ()


@dataclass(frozen=True)
class _GradeListAtom:
    end: int
    first_endpoint: tuple[str, int, bool, int]
    last_endpoint: tuple[str, int, bool, int]
    semantic_terminal: tuple[str, int, bool, int]
    grades: tuple[str, ...]
    valid: bool
    is_range: bool
    has_explicit_anchor: bool


@dataclass(frozen=True)
class _NormalizedGradeText:
    text: str
    list_facts: tuple[tuple[int, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class TitleCandidate:
    kind: str
    title: str
    score: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str):
            raise TypeError("kind must be a string")
        if not isinstance(self.title, str):
            raise TypeError("title must be a string")
        if type(self.score) is not int:
            raise TypeError("score must be an integer")
        object.__setattr__(self, "reasons", _tuple_of_strings(self.reasons, "reasons"))


@dataclass(frozen=True)
class TitleDecision:
    title: str
    candidates: tuple[TitleCandidate, ...]
    selected_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.title, str):
            raise TypeError("title must be a string")
        if not isinstance(self.selected_kind, str):
            raise TypeError("selected_kind must be a string")
        if not isinstance(self.candidates, (list, tuple)):
            raise TypeError("candidates must be a list or tuple of TitleCandidate")
        if any(not isinstance(candidate, TitleCandidate) for candidate in self.candidates):
            raise TypeError("candidates must contain only TitleCandidate instances")
        object.__setattr__(self, "candidates", tuple(self.candidates))


def normalize_visible_title(value: object) -> str:
    if not isinstance(value, str):
        raise TitlePolicyError("TITLE_INVALID", "title must be a string")
    if any(
        category(character) in _REJECTED_TITLE_CATEGORIES and character != "\t"
        for character in value
    ):
        raise TitlePolicyError("TITLE_INVALID", "title contains a forbidden control character")

    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized:
        raise TitlePolicyError("TITLE_INVALID", "title must not be empty")
    return normalized


def normalize_publication_wording(value: str) -> str:
    """Replace disallowed promotional product wording in publication copy."""
    return replace_publication_wording(value)


def validate_title_length(value: object) -> str:
    title = normalize_publication_wording(normalize_visible_title(value))
    if not MIN_TITLE_LENGTH <= len(title) <= MAX_TITLE_LENGTH:
        raise TitlePolicyError(
            "TITLE_LENGTH",
            f"title length must be from {MIN_TITLE_LENGTH} to {MAX_TITLE_LENGTH} characters",
        )
    return title


def _prefix(facts: TitleFacts) -> str:
    return "".join(facts.grades) + "".join(facts.subjects)


def _ordered_match_records(
    texts: tuple[str, ...], terms: tuple[str, ...]
) -> tuple[tuple[int, int, int, str], ...]:
    """Return non-overlapping source records, preferring the longest term."""
    matches: list[tuple[int, int, int, str]] = []
    for text_index, text in enumerate(texts):
        for term_order, term in enumerate(terms):
            start = text.find(term)
            while start >= 0:
                matches.append((text_index, start, term_order, term))
                start = text.find(term, start + 1)

    matches.sort(key=lambda item: (-len(item[3]), item[0], item[1], item[2]))
    selected_matches: list[tuple[int, int, int, str]] = []
    trees = {
        text_index: ([0] * (len(text) + 2), [0] * (len(text) + 2))
        for text_index, text in enumerate(texts)
    }

    def add(tree: list[int], index: int, delta: int) -> None:
        index += 1
        while index < len(tree):
            tree[index] += delta
            index += index & -index

    def total(tree: list[int], count: int) -> int:
        result = 0
        while count:
            result += tree[count]
            count -= count & -count
        return result

    def prefix(first: list[int], second: list[int], count: int) -> int:
        return total(first, count) * count - total(second, count)

    def range_total(first: list[int], second: list[int], start: int, end: int) -> int:
        return prefix(first, second, end) - prefix(first, second, start)

    def mark(first: list[int], second: list[int], start: int, end: int) -> None:
        add(first, start, 1)
        add(first, end, -1)
        add(second, start, start)
        add(second, end, -end)

    for text_index, start, term_order, term in matches:
        end = start + len(term)
        first, second = trees[text_index]
        if range_total(first, second, start, end):
            continue
        mark(first, second, start, end)
        selected_matches.append((text_index, start, term_order, term))
    selected_matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return tuple(selected_matches)


def _ordered_matches(texts: tuple[str, ...], terms: tuple[str, ...]) -> tuple[str, ...]:
    """Return non-overlapping terms in source order, preferring the longest match."""
    selected: list[str] = []
    for _, _, _, term in _ordered_match_records(texts, terms):
        if term not in selected:
            selected.append(term)
    return tuple(selected)


def _all_ordered_matches(texts: tuple[str, ...], terms: tuple[str, ...]) -> tuple[str, ...]:
    """Return every distinct source term, including overlapping risk names."""
    matches: list[tuple[int, int, int, str]] = []
    for text_index, text in enumerate(texts):
        for term_order, term in enumerate(terms):
            start = text.find(term)
            while start >= 0:
                matches.append((text_index, start, term_order, term))
                start = text.find(term, start + 1)
    matches.sort(key=lambda item: (item[0], item[1], -len(item[3]), item[2]))
    selected: list[str] = []
    for _, _, _, term in matches:
        if term not in selected:
            selected.append(term)
    return tuple(selected)


def _mapped_facts(texts: tuple[str, ...], mapping: MappingProxyType) -> tuple[str, ...]:
    terms = tuple(mapping.keys())
    results: list[str] = []
    for match in _ordered_matches(texts, terms):
        value = mapping[match]
        if value not in results:
            results.append(value)
    return tuple(results)


_DOMINANCE = {
    "benefit": {"配套音频": ("音频",), "可打印文档": ("可打印",), "答案解析": ("解析",)},
    "format": {"可打印练习试卷": ("可打印试卷", "练习试卷", "试卷", "练习"), "可打印试卷": ("试卷",), "练习试卷": ("试卷", "练习"), "专项练习": ("练习",), "专题讲义": ("讲义",), "视频课程": ("视频",), "学习资料包": ("资料包",)},
    "scenario": {"期末专项复习": ("期末",), "期中专项复习": ("期中",), "暑假预习": ("暑假",), "寒假预习": ("寒假",), "开学衔接": ("开学",), "一轮复习": ("一轮",), "二轮复习": ("二轮",), "同步学习": ("同步",)},
}


def _remove_dominated(values: tuple[str, ...], group: str) -> tuple[str, ...]:
    dominated = {item for value in values for item in _DOMINANCE[group].get(value, ())}
    return tuple(value for value in values if value not in dominated)


def _extract_grades(
    texts: tuple[str, ...], list_facts: tuple[tuple[tuple[int, tuple[str, ...]], ...], ...] = ()
) -> tuple[str, ...]:
    events: list[tuple[int, int, int, tuple[str, ...]]] = [
        (text_index, start, 1, (term,))
        for text_index, start, _, term in _ordered_match_records(texts, GRADE_TERMS)
    ]
    for text_index, text_facts in enumerate(list_facts):
        for start, grades in text_facts:
            events.append((text_index, start, 0, grades))
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    grades: list[str] = []
    for _, _, _, event_grades in events:
        for grade in event_grades:
            if grade not in grades:
                grades.append(grade)
    generic_for_specific = {
        "小学": ("一年级", "二年级", "三年级", "四年级", "五年级", "六年级", *_RANGE_GRADE_TERMS),
        "初中": ("七年级", "八年级", "九年级", *_RANGE_GRADE_TERMS),
        "高中": ("高一", "高二", "高三", "高一高二高三", *_RANGE_GRADE_TERMS, *_HIGH_RANGE_TERMS),
    }
    for generic, specific_terms in generic_for_specific.items():
        if generic in grades and any(term in grades for term in specific_terms):
            grades.remove(generic)
    return tuple(grades)


def _read_grade_endpoint(value: str, start: int) -> tuple[str, int, bool, int] | None:
    """Read one complete grade endpoint; multi-digit/numeral tokens stay indivisible."""
    if start >= len(value):
        return None
    index = start
    kind: str
    token: str
    if value[index] in {"高", "初"}:
        marker = value[index]
        index += 1
        number_start = index
        while index < len(value) and value[index] in _CHINESE_GRADE_NUMBER_CHARACTERS:
            index += 1
        if index == number_start:
            return None
        kind = "high" if marker == "高" else "middle"
        token = value[number_start:index]
    elif value[index].isdigit():
        while index < len(value) and value[index].isdigit():
            index += 1
        kind = "primary-ascii"
        token = value[start:index]
    elif value[index] in _CHINESE_GRADE_NUMBER_CHARACTERS:
        while index < len(value) and value[index] in _CHINESE_GRADE_NUMBER_CHARACTERS:
            index += 1
        kind = "primary-chinese"
        token = value[start:index]
    else:
        return None
    has_grade_suffix = value.startswith("年级", index)
    if has_grade_suffix:
        index += 2
    if kind == "high":
        number = {"一": 1, "二": 2, "三": 3}.get(token, 0)
    elif kind == "middle":
        number = {"一": 7, "二": 8, "三": 9}.get(token, 0)
    elif kind == "primary-ascii":
        number = int(token) if len(token) == 1 and token in _NUMERIC_SINGLE_GRADES else 0
    else:
        number = _CHINESE_K12_VALUES.get(token, 0)
    return kind, number, has_grade_suffix, index


def _skip_horizontal_space(value: str, index: int) -> int:
    while index < len(value) and value[index] in {" ", "\t"}:
        index += 1
    return index


def _quantity_boundary_kind(value: str, index: int) -> str:
    """Classify a complete next quantity separately from a right-endpoint unit."""
    if _QUANTITY_RE.match(value, index) or _CHINESE_QUANTITY_RE.match(value, index):
        return "next_quantity"
    if any(value.startswith(term, index) for term in _QUANTITY_TERMS):
        return "attached_quantity"
    return ""


def _range_boundary(value: str, index: int) -> str:
    index = _skip_horizontal_space(value, index)
    if index >= len(value):
        return "end"
    quantity_kind = _quantity_boundary_kind(value, index)
    if quantity_kind:
        return quantity_kind
    if value[index] in _RANGE_PUNCTUATION:
        return "punctuation"
    fact_terms = (*SUBJECT_TERMS, *SCENARIO_MAP, *FORMAT_MAP, *BENEFIT_MAP, "资料")
    if any(value.startswith(term, index) for term in fact_terms):
        return "fact"
    return "other"


def _canonical_scanned_range(
    endpoints: list[tuple[str, int, bool, int]],
    connectors: list[str],
    boundary: str,
) -> str | None:
    if len(endpoints) != 2 or len(connectors) != 1 or connectors[0] not in _GRADE_RANGE_CONNECTORS:
        return "范围保护"
    left_kind, left_value, left_has_suffix, _ = endpoints[0]
    right_kind, right_value, right_has_suffix, _ = endpoints[1]
    if left_kind in {"high", "middle"} and right_kind == "primary-chinese":
        right_kind = left_kind
        right_value = {1: 1, 2: 2, 3: 3}.get(right_value, 0) if left_kind == "high" else {1: 7, 2: 8, 3: 9}.get(right_value, 0)
    if boundary == "other" and not (
        right_has_suffix or right_kind in {"high", "middle"}
    ):
        return "范围保护"
    if left_kind != right_kind or not left_value or not right_value or left_value >= right_value:
        return "范围保护"
    if left_kind in {"primary-ascii", "primary-chinese"}:
        if not (left_has_suffix or right_has_suffix or boundary in {"end", "punctuation", "fact", "next_quantity"}):
            return "范围保护"
        return f"{_CHINESE_GRADE_DIGITS[left_value]}至{_CHINESE_GRADE_DIGITS[right_value]}年级"
    if left_kind == "high":
        return f"高{_CHINESE_GRADE_DIGITS[left_value]}至高{_CHINESE_GRADE_DIGITS[right_value]}"
    return f"{_CHINESE_GRADE_DIGITS[left_value]}至{_CHINESE_GRADE_DIGITS[right_value]}年级"


def _consume_suspicious_range_tail(value: str, index: int) -> tuple[int, bool]:
    """Consume a bounded connector/endpoint run so malformed parts cannot re-match."""
    cursor = index
    saw_endpoint = False
    while cursor < len(value):
        cursor = _skip_horizontal_space(value, cursor)
        if cursor < len(value) and value[cursor] in _GRADE_COMPOSITE_CONNECTORS:
            cursor += 1
            continue
        if _quantity_boundary_kind(value, cursor):
            break
        endpoint = _read_grade_endpoint(value, cursor)
        if endpoint is None:
            break
        saw_endpoint = True
        cursor = endpoint[3]
    return cursor, saw_endpoint


def _is_explicit_grade_endpoint(endpoint: tuple[str, int, bool, int]) -> bool:
    return endpoint[2] or endpoint[0] in {"high", "middle"}


def _has_course_label_before(value: str, start: int) -> bool:
    cursor = start
    while cursor > 0 and value[cursor - 1] in {" ", "\t"}:
        cursor -= 1
    return any(
        cursor >= len(label) and value[cursor - len(label):cursor] == label
        for label in ("课程编号", "课程")
    )


def _is_number_label_before_grade(
    value: str, start: int, left: tuple[str, int, bool, int], connector_index: int
) -> bool:
    raw_token = value[start:left[3]].removesuffix("年级")
    if (
        left[0] != "primary-ascii"
        or left[2]
        or value[connector_index] not in _NUMBER_LABEL_SEPARATORS
    ):
        return False
    is_year = len(raw_token) == 4 and raw_token[:2] in {"19", "20"}
    is_course_number = (
        not is_year
        and _has_course_label_before(value, start)
        and value[connector_index] in {"-", "—", "–"}
    )
    if not (is_year or is_course_number):
        return False
    right = _read_grade_endpoint(value, _skip_horizontal_space(value, connector_index + 1))
    if not right:
        return False
    right_can_start_range = (
        _skip_horizontal_space(value, right[3]) < len(value)
        and value[_skip_horizontal_space(value, right[3])]
        in (*_GRADE_RANGE_CONNECTORS, *_GRADE_LIST_SEPARATORS)
    )
    if not (
        right[1]
        or right[2]
        or right[0] in {"high", "middle", "primary-chinese"}
        or (right[0] == "primary-ascii" and right_can_start_range)
    ):
        return False
    return is_year or (_is_explicit_grade_endpoint(right) or right_can_start_range)


def _canonical_list_grade(endpoint: tuple[str, int, bool, int]) -> str:
    kind, number, _, _ = endpoint
    if kind == "high":
        return f"高{_CHINESE_GRADE_DIGITS[number]}"
    return f"{_CHINESE_GRADE_DIGITS[number]}年级"


def _semantic_range_terminal(
    endpoints: list[tuple[str, int, bool, int]],
) -> tuple[str, int, bool, int]:
    """Preserve the inferred initial/high system for later list-state checks."""
    left_kind, _, _, _ = endpoints[0]
    right_kind, right_value, right_has_suffix, right_end = endpoints[-1]
    if left_kind == "high" and right_kind == "primary-chinese":
        return "high", {1: 1, 2: 2, 3: 3}.get(right_value, 0), right_has_suffix, right_end
    if left_kind == "middle" and right_kind == "primary-chinese":
        return "middle", {1: 7, 2: 8, 3: 9}.get(right_value, 0), right_has_suffix, right_end
    return endpoints[-1]


def _list_separator_after_range_connectors(value: str, index: int) -> int | None:
    """Return a trailing list separator after one or more otherwise dangling range signs."""
    cursor = index
    saw_connector = False
    while True:
        cursor = _skip_horizontal_space(value, cursor)
        if cursor >= len(value) or value[cursor] not in _GRADE_RANGE_CONNECTORS:
            break
        saw_connector = True
        cursor += 1
    return cursor if saw_connector and cursor < len(value) and value[cursor] in _GRADE_LIST_SEPARATORS else None


def _read_grade_list_atom(value: str, start: int) -> _GradeListAtom | None:
    """Read one list atom, preserving a complete range as one verified fact."""
    first = _read_grade_endpoint(value, start)
    if first is None:
        return None
    connector_index = _skip_horizontal_space(value, first[3])
    if connector_index >= len(value) or value[connector_index] not in _GRADE_RANGE_CONNECTORS:
        return _GradeListAtom(
            first[3], first, first, first,
            (_canonical_list_grade(first),) if first[1] else (), bool(first[1]), False,
            _is_explicit_grade_endpoint(first),
        )

    endpoints = [first]
    connectors: list[str] = []
    cursor = connector_index
    while cursor < len(value) and value[cursor] in _GRADE_RANGE_CONNECTORS:
        list_separator = _list_separator_after_range_connectors(value, cursor)
        if list_separator is not None:
            return _GradeListAtom(
                list_separator, first, endpoints[-1], endpoints[-1], (), False, True, False
            )
        right_start = _skip_horizontal_space(value, cursor + 1)
        if _quantity_boundary_kind(value, right_start):
            if len(endpoints) == 1:
                return _GradeListAtom(
                    first[3], first, first, first,
                    (_canonical_list_grade(first),) if first[1] else (), bool(first[1]), False,
                    _is_explicit_grade_endpoint(first),
                )
            break
        right = _read_grade_endpoint(value, right_start)
        if right is None:
            tail_end, saw_endpoint = _consume_suspicious_range_tail(value, cursor)
            if len(endpoints) > 1 and not saw_endpoint:
                break
            if not saw_endpoint:
                return _GradeListAtom(
                    first[3], first, first, first,
                    (_canonical_list_grade(first),) if first[1] else (), bool(first[1]), False,
                    _is_explicit_grade_endpoint(first),
                )
            return _GradeListAtom(
                tail_end, first, endpoints[-1], endpoints[-1], (), False, True, False
            )
        connectors.append(value[cursor])
        endpoints.append(right)
        cursor = _skip_horizontal_space(value, right[3])

    canonical = _canonical_scanned_range(
        endpoints, connectors, _range_boundary(value, endpoints[-1][3])
    )
    valid = canonical is not None and canonical != "范围保护"
    return _GradeListAtom(
        endpoints[-1][3], first, endpoints[-1], _semantic_range_terminal(endpoints),
        (canonical,) if valid else (), valid, True,
        any(_is_explicit_grade_endpoint(endpoint) for endpoint in endpoints),
    )


def _scan_grade_list(
    value: str, initial_atom: _GradeListAtom, connector_index: int
) -> _GradeListScan | None:
    """Consume one grade list and return only scanner-verified facts."""
    if value[connector_index] not in _GRADE_LIST_SEPARATORS:
        return None
    left = initial_atom.first_endpoint
    cursor = connector_index
    left_is_chinese_ellipsis = (
        not initial_atom.is_range
        and left[0] == "primary-chinese"
        and bool(left[1])
        and not left[2]
    )
    ambiguous = not initial_atom.valid or (
        not initial_atom.is_range
        and not _is_explicit_grade_endpoint(left)
        and not left_is_chinese_ellipsis
    )
    previous_endpoint = initial_atom.semantic_terminal
    verified_grades = list(initial_atom.grades)
    saw_endpoint = False
    while cursor < len(value) and value[cursor] in _GRADE_LIST_SEPARATORS:
        separator_count = 0
        while True:
            cursor = _skip_horizontal_space(value, cursor)
            if cursor >= len(value) or value[cursor] not in _GRADE_LIST_SEPARATORS:
                break
            cursor += 1
            separator_count += 1
        right_start = _skip_horizontal_space(value, cursor)
        if _quantity_boundary_kind(value, right_start):
            break
        atom = _read_grade_list_atom(value, right_start)
        if atom is None:
            break
        saw_endpoint = True
        right = atom.first_endpoint
        verified_grades.extend(atom.grades)
        right_is_chinese_ellipsis = (
            right[0] == "primary-chinese" and bool(right[1]) and not right[2]
        )
        if (
            separator_count > 1
            or not atom.valid
            or (
                not _is_explicit_grade_endpoint(right)
                and not atom.is_range
                and not (left_is_chinese_ellipsis and right_is_chinese_ellipsis)
            )
            or (
                previous_endpoint[0] in {"high", "middle"}
                and not previous_endpoint[2]
                and (not atom.is_range or not atom.has_explicit_anchor)
                and right[0].startswith("primary")
                and right[1] <= 3
            )
        ):
            ambiguous = True
        cursor = _skip_horizontal_space(value, atom.end)
        previous_endpoint = atom.semantic_terminal
        if (
            left_is_chinese_ellipsis
            and not ambiguous
            and atom.valid
            and atom.last_endpoint[0].startswith("primary")
            and atom.last_endpoint[2]
        ):
            return _GradeListScan(cursor, ambiguous, tuple(verified_grades))
    if left_is_chinese_ellipsis:
        ambiguous = True
    return (
        _GradeListScan(cursor, ambiguous, tuple(verified_grades))
        if saw_endpoint
        else None
    )


def _canonical_single_grade(
    endpoint: tuple[str, int, bool, int], raw: str
) -> str:
    kind, number, has_grade_suffix, _ = endpoint
    if not number or (kind.startswith("primary") and not has_grade_suffix):
        return raw
    return _canonical_list_grade(endpoint)


def _normalize_grade_text(value: str) -> _NormalizedGradeText:
    """Perform one bounded lexical pass so invalid range samples cannot leak endpoints."""
    output: list[str] = []
    output_length = 0
    list_facts: list[tuple[int, tuple[str, ...]]] = []

    def emit(part: str) -> None:
        nonlocal output_length
        output.append(part)
        output_length += len(part)

    index = 0
    while index < len(value):
        left = _read_grade_endpoint(value, index)
        if left is None:
            emit(value[index])
            index += 1
            continue
        connector_index = _skip_horizontal_space(value, left[3])
        if (
            not left[1]
            and connector_index < len(value)
            and _is_number_label_before_grade(value, index, left, connector_index)
        ):
            emit(value[index:left[3]])
            index = left[3]
            continue
        initial_atom = _read_grade_list_atom(value, index)
        list_connector = (
            _skip_horizontal_space(value, initial_atom.end)
            if initial_atom is not None
            else connector_index
        )
        list_scan = (
            _scan_grade_list(value, initial_atom, list_connector)
            if initial_atom is not None and list_connector < len(value)
            else None
        )
        if list_scan is not None:
            if list_scan.ambiguous:
                emit("范围保护")
            else:
                list_facts.append((output_length, list_scan.grades))
                emit("范围保护")
            index = list_scan.end
            continue
        if connector_index >= len(value) or value[connector_index] not in _GRADE_COMPOSITE_CONNECTORS:
            raw = value[index:left[3]]
            emit(
                "范围保护"
                if not left[1] and (left[2] or left[0] in {"high", "middle"})
                else _canonical_single_grade(left, raw)
            )
            index = left[3]
            continue
        endpoints = [left]
        connectors: list[str] = []
        cursor = connector_index
        while cursor < len(value) and value[cursor] in _GRADE_COMPOSITE_CONNECTORS:
            right_start = _skip_horizontal_space(value, cursor + 1)
            right = _read_grade_endpoint(value, right_start)
            if right is None:
                break
            connectors.append(value[cursor])
            endpoints.append(right)
            cursor = _skip_horizontal_space(value, right[3])
        if len(endpoints) == 1:
            suspicious_end, saw_endpoint = _consume_suspicious_range_tail(value, connector_index)
            if saw_endpoint:
                emit("范围保护")
                index = suspicious_end
            else:
                emit("范围保护" if not left[1] else value[index:left[3]])
                index = left[3]
            continue
        boundary = _range_boundary(value, endpoints[-1][3])
        if boundary == "attached_quantity":
            emit(
                "范围保护"
                if not endpoints[0][1]
                else _canonical_single_grade(endpoints[0], value[index:left[3]])
            )
            index = left[3]
            continue
        if boundary == "next_quantity" and (
            endpoints[0][2] or endpoints[0][0] in {"high", "middle"}
        ) and not (
            endpoints[-1][2] or endpoints[-1][0] in {"high", "middle"}
        ):
            emit(_canonical_single_grade(endpoints[0], value[index:left[3]]))
            index = left[3]
            continue
        canonical = _canonical_scanned_range(endpoints, connectors, boundary)
        emit(canonical or "范围保护")
        index = endpoints[-1][3]
    return _NormalizedGradeText("".join(output), tuple(list_facts))


def _extract_quantities(texts: tuple[str, ...]) -> tuple[str, ...]:
    quantities: list[str] = []
    for text in texts:
        for match in _QUANTITY_RE.finditer(text):
            quantity = "".join(match.groups())
            if quantity not in quantities:
                quantities.append(quantity)
    return tuple(quantities)


def extract_title_facts(
    values: tuple[str, ...], *, risk_terms: tuple[str, ...] = RISK_TERMS
) -> TitleFacts:
    """Extract verified title facts after removing source-specific risk names."""
    source_values = _tuple_of_strings(values, "values")
    supplied_risk_terms = _dedupe_strings((*RISK_TERMS, *_tuple_of_strings(risk_terms, "risk_terms")))
    normalized_values: list[str] = []
    for value in source_values:
        if value == "":
            continue
        try:
            normalized = normalize_visible_title(value)
        except TitlePolicyError as error:
            if error.code == "TITLE_INVALID" and _is_ignorable_blank(value):
                continue
            raise
        normalized_values.append(normalized)

    blocked_terms = _all_ordered_matches(tuple(normalized_values), supplied_risk_terms)
    removal_terms = tuple(sorted(set(supplied_risk_terms), key=len, reverse=True))
    cleaned_values = tuple(
        _normalize_cleaned_value(
            re.sub(r"[《》【】]", " ", _remove_terms(value, removal_terms))
        )
        for value in normalized_values
    )
    grade_normalizations = tuple(
        _normalize_grade_text(value)
        for value in cleaned_values if value
    )
    cleaned_values = tuple(normalization.text for normalization in grade_normalizations)
    return TitleFacts(
        source_texts=cleaned_values,
        grades=_extract_grades(
            cleaned_values,
            tuple(normalization.list_facts for normalization in grade_normalizations),
        ),
        subjects=_ordered_matches(cleaned_values, SUBJECT_TERMS),
        scenarios=_remove_dominated(_mapped_facts(cleaned_values, SCENARIO_MAP), "scenario"),
        formats=_remove_dominated(_mapped_facts(cleaned_values, FORMAT_MAP), "format"),
        quantities=_extract_quantities(cleaned_values),
        benefits=_remove_dominated(_mapped_facts(cleaned_values, BENEFIT_MAP), "benefit"),
        blocked_terms=blocked_terms,
        risk_terms=supplied_risk_terms,
    )


def _remove_terms(value: str, terms: tuple[str, ...]) -> str:
    cleaned = value
    for term in terms:
        if term:
            cleaned = cleaned.replace(term, " ")
    return cleaned


def _normalize_cleaned_value(value: str) -> str:
    if not value.strip():
        return ""
    return normalize_visible_title(value)


def _is_ignorable_blank(value: str) -> bool:
    return bool(value) and all(character == "\t" or category(character) == "Zs" for character in value)


def _dedupe_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def _format_covers_benefit(benefit: str, formats: list[str] | tuple[str, ...]) -> bool:
    return any(format_ in _FORMAT_COVERED_BENEFITS.get(benefit, ()) for format_ in formats)


def _layout_parts(facts: TitleFacts, layout: str) -> tuple[list[str], list[str]]:
    prefix = _prefix(facts)
    primary_scenario = list(facts.scenarios[:1])
    if layout == "leading":
        return [part for part in (prefix, *primary_scenario) if part], []
    if layout == "omitted":
        return [part for part in (prefix,) if part], []
    if layout == "trailing":
        return [part for part in (prefix,) if part], primary_scenario
    raise ValueError(f"unknown candidate layout: {layout}")


def _select_display_detail_facts(
    facts: TitleFacts,
    kind: str,
    *,
    separator: str = "",
    zero_cost_benefits: frozenset[str] = frozenset(),
    priority_groups: tuple[str, ...] | None = None,
    layout: str = "leading",
) -> dict[str, list[str]]:
    """Retain complete display atoms in priority order within the title limit."""
    leading_parts, trailing_parts = _layout_parts(facts, layout)
    displayed_parts = [*leading_parts, *trailing_parts]
    current_length = sum(len(part) for part in displayed_parts)
    current_length += max(0, len(displayed_parts) - 1) * len(separator)
    selected = {"scenario": [], "format": [], "quantity": [], "benefit": []}
    used: set[str] = set(facts.scenarios[:1])
    group_values = {
        "scenario": facts.scenarios[1:],
        "format": facts.formats,
        "quantity": facts.quantities,
        "benefit": facts.benefits,
    }
    priorities = {
        "search": ("quantity", "format", "benefit", "scenario"),
        "scenario": ("format", "quantity", "benefit", "scenario"),
        "value": ("benefit", "format", "quantity", "scenario"),
    }
    source_groups = tuple(
        (group, group_values[group]) for group in (priority_groups or priorities[kind])
    )
    for group, values in source_groups:
        for value in values:
            if (
                not value
                or value in used
                or (group == "benefit" and value in zero_cost_benefits)
                or current_length + len(value) + (len(separator) if displayed_parts else 0) > MAX_TITLE_LENGTH
            ):
                continue
            selected[group].append(value)
            used.add(value)
            current_length += len(value) + (len(separator) if displayed_parts else 0)
            displayed_parts.append(value)
    return selected


def _selected_detail_facts(
    facts: TitleFacts,
    kind: str,
    *,
    separator: str = "",
    priority_groups: tuple[str, ...] | None = None,
    layout: str = "leading",
) -> dict[str, list[str]]:
    """Select display facts, then retain format-covered benefits at zero display cost."""
    potentially_covered = frozenset(
        benefit
        for benefit in facts.benefits
        if _format_covers_benefit(benefit, facts.formats)
    )
    if not potentially_covered:
        return _select_display_detail_facts(
            facts, kind, separator=separator, priority_groups=priority_groups, layout=layout
        )

    groups = _select_display_detail_facts(
        facts,
        kind,
        separator=separator,
        zero_cost_benefits=potentially_covered,
        priority_groups=priority_groups,
        layout=layout,
    )
    covered = tuple(
        benefit
        for benefit in facts.benefits
        if benefit in potentially_covered
        and _format_covers_benefit(benefit, groups["format"])
    )
    if not covered:
        return _select_display_detail_facts(
            facts, kind, separator=separator, priority_groups=priority_groups, layout=layout
        )

    groups["benefit"].extend(covered)
    return groups


@dataclass(frozen=True)
class _BuiltCandidate:
    title: str
    quantities: tuple[str, ...]
    benefits: tuple[str, ...]


def _build_candidate(
    facts: TitleFacts,
    kind: str,
    *,
    variant: bool = False,
    separator: str = "",
    selection_priority: tuple[str, ...] | None = None,
    layout: str = "leading",
) -> _BuiltCandidate:
    groups = _selected_detail_facts(
        facts,
        kind,
        separator=separator,
        priority_groups=selection_priority,
        layout=layout,
    )
    orders = {
        "search": ("scenario", "quantity", "format", "benefit"),
        "scenario": ("scenario", "format", "quantity", "benefit"),
        "value": ("benefit", "format", "quantity", "scenario"),
    }
    leading_parts, trailing_parts = _layout_parts(facts, layout)
    parts = [*leading_parts]
    retained = {"quantity": [], "benefit": []}
    for group in orders[kind]:
        values = groups[group]
        if variant and kind == "scenario":
            values = list(reversed(values))
        elif variant and kind == "value" and len(values) > 1:
            values = [*values[1:], values[0]]
        for value in values:
            if group == "benefit" and _format_covers_benefit(value, groups["format"]):
                retained[group].append(value)
                continue
            added, replaced = _append_nonredundant(parts, value)
            if added and group in retained:
                if replaced:
                    for metadata in retained.values():
                        if replaced in metadata:
                            metadata.remove(replaced)
                retained[group].append(value)
    parts.extend(trailing_parts)
    return _BuiltCandidate(
        title=separator.join(part for part in parts if part),
        quantities=tuple(retained["quantity"]),
        benefits=tuple(retained["benefit"]),
    )


def _append_nonredundant(parts: list[str], value: str) -> tuple[bool, str | None]:
    if value in parts:
        return False, None
    parts.append(value)
    return True, None


def _build_candidate_title(facts: TitleFacts, kind: str) -> str:
    return _build_candidate(facts, kind).title


def _score(
    title: str,
    facts: TitleFacts,
    kind: str,
    selected_quantities: tuple[str, ...] = (),
    selected_benefits: tuple[str, ...] = (),
) -> tuple[int, tuple[str, ...]]:
    score = 0
    reasons: list[str] = []
    prefix = _prefix(facts)
    if prefix and title.startswith(prefix) and len(prefix) <= 15:
        score += 60
        reasons.append("年级学科置于标题开头")
    if any(scenario in title[:30] for scenario in facts.scenarios):
        score += 20
        reasons.append("场景位于前30字")
    if selected_quantities:
        score += min(8, len(selected_quantities) * 2)
        reasons.append("包含经验证数量")
    if selected_benefits:
        score += min(8, len(selected_benefits) * 2)
        reasons.append("包含经验证利益点")
    if kind == "search":
        score += 1
        reasons.append("搜索结构同分优先")
    return score, tuple(reasons)


def _ordered_atom_values(values: tuple[str, ...], variant: int) -> tuple[str, ...]:
    if variant == 0 or len(values) < 2:
        return values
    if variant == 1:
        return tuple(reversed(values))
    return (*values[1:], values[0])


def _bounded_factual_candidates(facts: TitleFacts) -> tuple[_BuiltCandidate, ...]:
    """Enumerate a deterministic, bounded set of whole-fact title structures."""
    groups = {
        "scenario": facts.scenarios,
        "quantity": facts.quantities,
        "format": facts.formats,
        "benefit": facts.benefits,
    }
    active_groups = tuple(group for group, values in groups.items() if values)
    if not active_groups:
        return ()
    found: dict[str, _BuiltCandidate] = {}
    plan_count = 0
    for group_order in permutations(active_groups):
        for variant in range(3):
            if plan_count >= _MAX_FACTUAL_CANDIDATE_PLANS:
                break
            plan_count += 1
            parts = [_prefix(facts)] if _prefix(facts) else []
            quantities: list[str] = []
            benefits: list[str] = []
            selected_formats: list[str] = []
            deferred_printable = False
            for group in group_order:
                for value in _ordered_atom_values(groups[group], variant):
                    if not value or value in parts:
                        continue
                    if group == "benefit" and value == "可打印":
                        if _format_covers_benefit(value, selected_formats):
                            if value not in benefits:
                                benefits.append(value)
                            continue
                        if _format_covers_benefit(value, list(facts.formats)):
                            deferred_printable = True
                            continue
                    if len("".join(parts)) + len(value) > MAX_TITLE_LENGTH:
                        continue
                    parts.append(value)
                    if group == "quantity":
                        quantities.append(value)
                    elif group == "benefit":
                        benefits.append(value)
                    elif group == "format":
                        selected_formats.append(value)
                        if (
                            deferred_printable
                            and "可打印" not in benefits
                            and _format_covers_benefit("可打印", selected_formats)
                        ):
                            benefits.append("可打印")
                    title = "".join(parts)
                    try:
                        validate_final_title(title, facts)
                    except TitlePolicyError:
                        continue
                    candidate = _BuiltCandidate(title, tuple(quantities), tuple(benefits))
                    existing = found.get(title)
                    if existing is None or (
                        len(candidate.quantities) + len(candidate.benefits)
                        > len(existing.quantities) + len(existing.benefits)
                    ):
                        found[title] = candidate
        if plan_count >= _MAX_FACTUAL_CANDIDATE_PLANS:
            break
    return tuple(found.values())


def _evaluate_built_candidate(
    facts: TitleFacts,
    kind: str,
    forbidden_terms: tuple[str, ...],
    built: _BuiltCandidate,
) -> tuple[TitleCandidate | None, tuple[str, str, bool, str] | None]:
    title = built.title
    matched_risk = next((term for term in forbidden_terms if term and term in title), None)
    if matched_risk:
        return None, (kind, "RISK_TERM", False, matched_risk)
    try:
        validated_title = validate_final_title(title, facts)
    except TitlePolicyError as error:
        is_short = error.code == "TITLE_LENGTH" and len(title) < MIN_TITLE_LENGTH
        context = f"length={len(title)}" if error.code == "TITLE_LENGTH" else _safe_failure_message(error.code)
        return None, (kind, error.code, is_short, context)
    score, reasons = _score(validated_title, facts, kind, built.quantities, built.benefits)
    return TitleCandidate(kind=kind, title=validated_title, score=score, reasons=reasons), None


def _select_factual_candidates(
    facts: TitleFacts, forbidden_terms: tuple[str, ...]
) -> tuple[TitleCandidate, ...]:
    builds = _bounded_factual_candidates(facts)
    if not builds:
        return ()
    prefix = _prefix(facts)
    primary_scenario = facts.scenarios[0] if facts.scenarios else ""
    preferences = {
        "search": lambda built: 0 if primary_scenario and built.title.startswith(prefix + primary_scenario) else 1,
        "scenario": lambda built: 0 if primary_scenario and primary_scenario not in built.title else 1,
        "value": lambda built: 0 if primary_scenario and built.title.endswith(primary_scenario) else 1,
    }
    selected: list[TitleCandidate] = []
    used_titles: set[str] = set()
    for kind in ("search", "scenario", "value"):
        ordered = sorted(
            enumerate(builds),
            key=lambda indexed: (
                preferences[kind](indexed[1]),
                -len(indexed[1].quantities),
                -len(indexed[1].benefits),
                -len(indexed[1].title),
                indexed[0],
            ),
        )
        for _, built in ordered:
            if built.title in used_titles:
                continue
            candidate, _ = _evaluate_built_candidate(facts, kind, forbidden_terms, built)
            if candidate:
                selected.append(candidate)
                used_titles.add(candidate.title)
                break
    return tuple(selected)


def _evaluate_candidate(
    facts: TitleFacts,
    kind: str,
    forbidden_terms: tuple[str, ...],
    *,
    separator: str = "",
    selection_priority: tuple[str, ...] | None = None,
    layout: str = "leading",
) -> tuple[TitleCandidate | None, tuple[str, str, bool, str] | None]:
    built = _build_candidate(
        facts,
        kind,
        variant=True,
        separator=separator,
        selection_priority=selection_priority,
        layout=layout,
    )
    return _evaluate_built_candidate(facts, kind, forbidden_terms, built)


def select_best_title(facts: TitleFacts) -> TitleDecision:
    if not isinstance(facts, TitleFacts):
        raise TypeError("facts must be a TitleFacts instance")

    failures: list[tuple[str, str, bool, str]] = []
    policy_terms = _dedupe_strings((*RISK_TERMS, *facts.risk_terms, *facts.blocked_terms))
    forbidden_terms = tuple(
        term
        for _, term in sorted(
            enumerate(policy_terms), key=lambda indexed: (-len(indexed[1]), indexed[0])
        )
    )
    candidates = list(_select_factual_candidates(facts, forbidden_terms))
    if len(candidates) != 3:
        audit_failures: list[tuple[str, str, bool, str]] = []
        for kind in ("search", "scenario", "value"):
            _, failure = _evaluate_candidate(facts, kind, forbidden_terms)
            if failure:
                audit_failures.append(failure)
        failures = audit_failures

    if not candidates and all(
        code == "TITLE_LENGTH" and is_short for _, code, is_short, _ in failures
    ):
        raise TitlePolicyError(
            "TITLE_INFORMATION_INSUFFICIENT",
            "insufficient verified information for a compliant title",
        )
    if not candidates:
        summary = ", ".join(
            f"{kind}:{code}[{context}]" for kind, code, _, context in failures
        )
        raise TitlePolicyError("TITLE_POLICY_FAILED", f"candidate policy failed: {summary}")

    if len(candidates) != 3 or len({candidate.title for candidate in candidates}) != 3:
        raise TitlePolicyError(
            "TITLE_INFORMATION_INSUFFICIENT",
            "insufficient verified information for three distinct compliant titles",
        )

    selected = max(
        enumerate(candidates), key=lambda indexed: (indexed[1].score, -indexed[0])
    )[1]
    return TitleDecision(
        title=selected.title,
        candidates=tuple(candidates),
        selected_kind=selected.kind,
    )


def _safe_failure_message(code: str) -> str:
    return {
        "TITLE_INVALID": "title invalid",
        "TITLE_PREFIX": "prefix requirement failed",
        "TITLE_FACT_MISSING": "confirmed fact missing",
    }.get(code, "policy validation failed")


def validate_final_title(value: object, facts: TitleFacts) -> str:
    title = validate_title_length(value)

    for fact in (*facts.grades, *facts.subjects):
        if fact and fact not in title:
            raise TitlePolicyError(
                "TITLE_FACT_MISSING",
                f"title is missing confirmed fact: {fact}",
            )

    prefix = _prefix(facts)
    if prefix and (len(prefix) > 15 or not title.startswith(prefix)):
        raise TitlePolicyError(
            "TITLE_PREFIX",
            f"title must start with the confirmed prefix ({prefix})",
        )

    return title


def validate_publishable_title(value: object) -> str:
    """Validate a final title against the static policy enforceable at publish time."""
    title = validate_title_length(value)
    facts = extract_title_facts((title,))
    if facts.blocked_terms or any(term and term in title for term in RISK_TERMS):
        raise TitlePolicyError("TITLE_POLICY_FAILED", "title violates publication policy")
    return validate_final_title(title, facts)
