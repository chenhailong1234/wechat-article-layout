from dataclasses import FrozenInstanceError
from time import perf_counter

import pytest
import windows_capabilities.wechat_title_strategy as title_strategy

from windows_capabilities.wechat_title_strategy import (
    BENEFIT_MAP,
    FORMAT_MAP,
    GRADE_TERMS,
    MAX_TITLE_LENGTH,
    MIN_TITLE_LENGTH,
    RISK_TERMS,
    SCENARIO_MAP,
    SUBJECT_TERMS,
    TitleCandidate,
    TitleDecision,
    TitleFacts,
    TitlePolicyError,
    _ordered_matches,
    _bounded_factual_candidates,
    _build_candidate,
    _prefix,
    _score,
    extract_title_facts,
    normalize_visible_title,
    normalize_publication_wording,
    select_best_title,
    validate_title_length,
    validate_publishable_title,
    validate_final_title,
)


def test_normalize_publication_wording_replaces_course_terms_without_duplicates() -> None:
    assert normalize_publication_wording("小学数学课程训练营资料") == "小学数学资料"


def default_facts(**overrides: object) -> TitleFacts:
    values: dict[str, object] = {
        "source_texts": (
            "五年级数学期末专项复习资料 可打印试卷 含答案解析",
        ),
        "grades": ("五年级",),
        "subjects": ("数学",),
        "scenarios": ("期末专项复习",),
        "formats": ("可打印练习试卷",),
        "benefits": ("答案解析",),
    }
    values.update(overrides)
    return TitleFacts(**values)


def test_title_facts_candidate_and_decision_are_frozen_dataclasses() -> None:
    facts = default_facts()
    candidate = TitleCandidate(
        kind="fact-first",
        title="五年级数学期末专项复习资料（含答案解析）",
        score=90,
        reasons=("包含已确认事实",),
    )
    decision = TitleDecision(
        title=candidate.title,
        candidates=(candidate,),
        selected_kind=candidate.kind,
    )

    with pytest.raises(FrozenInstanceError):
        facts.grades = ("六年级",)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.title = "其他标题"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.title = "其他标题"  # type: ignore[misc]


def test_model_sequence_fields_copy_external_lists_to_tuples() -> None:
    source_texts = ["来源"]
    grades = ["五年级"]
    reasons = ["包含事实"]
    candidates = [
        TitleCandidate(kind="fact-first", title="标题", score=90, reasons=reasons)
    ]

    facts = TitleFacts(source_texts=source_texts, grades=grades)
    candidate = candidates[0]
    decision = TitleDecision(
        title="标题", candidates=candidates, selected_kind="fact-first"
    )
    source_texts.append("后来添加")
    grades.append("六年级")
    reasons.append("后来添加")
    candidates.append(candidate)

    assert facts.source_texts == ("来源",)
    assert facts.grades == ("五年级",)
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
        assert isinstance(getattr(facts, field_name), tuple)
    assert candidate.reasons == ("包含事实",)
    assert isinstance(candidate.reasons, tuple)
    assert decision.candidates == (candidate,)
    assert isinstance(decision.candidates, tuple)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TitleFacts(source_texts=["有效", 1]),
        lambda: TitleFacts(source_texts=("有效",), grades=[None]),
        lambda: TitleCandidate(
            kind="kind", title="title", score=1, reasons=["有效", 1]
        ),
        lambda: TitleDecision(title="title", candidates=["不是候选"], selected_kind="kind"),
    ],
)
def test_model_rejects_invalid_sequence_members(factory) -> None:
    with pytest.raises(TypeError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TitleFacts(source_texts="不是序列字段"),
        lambda: TitleCandidate(kind=1, title="title", score=1, reasons=()),
        lambda: TitleCandidate(kind="kind", title=1, score=1, reasons=()),
        lambda: TitleCandidate(kind="kind", title="title", score=1.5, reasons=()),
        lambda: TitleDecision(title="title", candidates=(), selected_kind=1),
    ],
)
def test_model_rejects_invalid_scalar_members(factory) -> None:
    with pytest.raises(TypeError):
        factory()


def test_default_fact_collections_are_empty_tuples() -> None:
    facts = TitleFacts(source_texts=("来源",))

    assert facts.grades == ()
    assert facts.subjects == ()
    assert facts.scenarios == ()
    assert facts.formats == ()
    assert facts.quantities == ()
    assert facts.benefits == ()
    assert facts.blocked_terms == ()
    assert facts.risk_terms == ()


def test_normalize_visible_title_collapses_ascii_and_full_width_whitespace() -> None:
    value = "  五年级数学\u3000 期末A卷（2026）  "

    normalized = normalize_visible_title(value)

    assert normalized == "五年级数学 期末A卷（2026）"
    assert len(normalized) == 16


@pytest.mark.parametrize("value", ["title\nline", "title\rline", "   \u3000  ", 123, None])
def test_normalize_visible_title_rejects_invalid_values(value: object) -> None:
    with pytest.raises(TitlePolicyError) as error:
        normalize_visible_title(value)

    assert error.value.code == "TITLE_INVALID"


@pytest.mark.parametrize("value", ["title\u0085line", "title\u2028line", "title\u2029line"])
def test_normalize_visible_title_rejects_unicode_line_boundaries(value: str) -> None:
    with pytest.raises(TitlePolicyError) as error:
        normalize_visible_title(value)

    assert error.value.code == "TITLE_INVALID"


@pytest.mark.parametrize("format_character", ["\u200b", "\ufeff", "\x00", "\x07"])
def test_validate_final_title_rejects_invisible_format_characters(
    format_character: str,
) -> None:
    title = "五年级数学" + "甲" * 24 + format_character

    with pytest.raises(TitlePolicyError) as error:
        validate_final_title(title, default_facts())

    assert error.value.code == "TITLE_INVALID"


@pytest.mark.parametrize("line_boundary", ["\v", "\f", "\x1c", "\x1d", "\x1e"])
def test_normalize_visible_title_rejects_control_line_boundaries(line_boundary: str) -> None:
    with pytest.raises(TitlePolicyError) as error:
        normalize_visible_title(f"标题{line_boundary}内容")

    assert error.value.code == "TITLE_INVALID"


def test_normalize_visible_title_collapses_tabs_as_whitespace() -> None:
    assert normalize_visible_title("五年级数学\t\t期末复习") == "五年级数学 期末复习"


def test_title_length_constants_define_inclusive_bounds() -> None:
    assert MIN_TITLE_LENGTH == 30
    assert MAX_TITLE_LENGTH == 60


@pytest.mark.parametrize("length", [MIN_TITLE_LENGTH, MAX_TITLE_LENGTH])
def test_validate_title_length_normalizes_and_allows_inclusive_bounds(length: int) -> None:
    title = "五年级数学" + "甲" * (length - len("五年级数学"))

    assert validate_title_length(f"  {title}  ") == title


@pytest.mark.parametrize("length", [MIN_TITLE_LENGTH - 1, MAX_TITLE_LENGTH + 1])
def test_validate_title_length_rejects_titles_outside_inclusive_bounds(length: int) -> None:
    title = "五年级数学" + "甲" * (length - len("五年级数学"))

    with pytest.raises(TitlePolicyError) as error:
        validate_title_length(title)

    assert error.value.code == "TITLE_LENGTH"
    assert "30 to 60" in str(error.value)


def test_validate_publishable_title_allows_strategy_selected_title() -> None:
    selected = select_best_title(
        extract_title_facts(
            (
                "五年级数学期末资料 28专题 可打印练习试卷",
                "含答案解析 100讲知识清单",
            )
        )
    ).title

    assert validate_publishable_title(selected) == selected


@pytest.mark.parametrize(
    "title,code,message",
    [
        (
            "五年级数学人教版期末专项复习资料可打印练习试卷答案解析完整合集",
            "TITLE_POLICY_FAILED",
            "title violates publication policy",
        ),
        (
            "期末资料五年级数学可打印练习试卷及答案解析完整合集知识清单重点",
            "TITLE_PREFIX",
            None,
        ),
    ],
)
def test_validate_publishable_title_rejects_risk_and_misplaced_prefix(
    title: str, code: str, message: str | None
) -> None:
    assert MIN_TITLE_LENGTH <= len(title) <= MAX_TITLE_LENGTH

    with pytest.raises(TitlePolicyError) as error:
        validate_publishable_title(title)

    assert error.value.code == code
    if message:
        assert str(error.value) == message


@pytest.mark.parametrize("length", [MIN_TITLE_LENGTH, MAX_TITLE_LENGTH])
def test_validate_final_title_allows_inclusive_length_bounds(length: int) -> None:
    title = "五年级数学" + "甲" * (length - len("五年级数学"))

    assert validate_final_title(title, default_facts()) == title


@pytest.mark.parametrize("length", [MIN_TITLE_LENGTH - 1, MAX_TITLE_LENGTH + 1])
def test_validate_final_title_rejects_titles_outside_length_bounds(length: int) -> None:
    title = "五年级数学" + "甲" * (length - len("五年级数学"))

    with pytest.raises(TitlePolicyError) as error:
        validate_final_title(title, default_facts())

    assert error.value.code == "TITLE_LENGTH"
    assert "30 to 60" in str(error.value)


def test_prefix_returns_contiguous_confirmed_grade_and_subject() -> None:
    assert _prefix(default_facts()) == "五年级数学"


def test_validate_final_title_returns_normalized_title() -> None:
    title = "  五年级数学 期末专项复习资料 可打印试卷 含答案解析 精选内容  "

    assert validate_final_title(title, default_facts()) == title.strip()


def test_validate_final_title_requires_prefix_at_title_start() -> None:
    title = "期末专项复习资料 五年级数学 含答案解析 可打印练习试卷 精选内容"

    with pytest.raises(TitlePolicyError) as error:
        validate_final_title(title, default_facts())

    assert error.value.code == "TITLE_PREFIX"
    assert "prefix" in str(error.value)


def test_validate_final_title_reports_missing_facts_before_prefix_mismatch() -> None:
    title = "五年级期末专项复习资料 可打印试卷 含答案解析 内容精选强化练习"

    with pytest.raises(TitlePolicyError) as error:
        validate_final_title(title, default_facts())

    assert error.value.code == "TITLE_FACT_MISSING"


def test_validate_final_title_rejects_prefix_longer_than_fifteen_characters() -> None:
    facts = default_facts(grades=("五年级",), subjects=("数学学科期末专项复习内容资料",))
    title = "五年级数学学科期末专项复习内容资料" + "甲" * 20

    with pytest.raises(TitlePolicyError) as error:
        validate_final_title(title, facts)

    assert error.value.code == "TITLE_PREFIX"
    assert "prefix" in str(error.value)


def test_title_policy_error_keeps_code_and_message() -> None:
    error = TitlePolicyError("CUSTOM", "说明")

    assert error.code == "CUSTOM"
    assert str(error) == "说明"


def test_title_fact_constants_cover_the_required_terms() -> None:
    assert {"一年级", "九年级", "高一", "高二", "高三", "一至六年级", "七至九年级", "高一高二高三", "小学", "初中", "高中"} <= set(GRADE_TERMS)
    assert {"语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "科学", "全科"} <= set(SUBJECT_TERMS)
    assert SCENARIO_MAP["期末"] == "期末"
    assert FORMAT_MAP["试卷"] == "试卷"
    assert BENEFIT_MAP["答案解析"] == "答案解析"
    assert {"人教版", "小状元", "学而思", "山东省", "学校"} <= set(RISK_TERMS)


def test_extract_title_facts_collects_only_cleaned_verified_facts() -> None:
    facts = extract_title_facts(("五年级数学期末资料", "可打印练习试卷 含答案解析"))

    assert facts.grades == ("五年级",)
    assert facts.subjects == ("数学",)
    assert facts.scenarios == ("期末",)
    assert facts.formats == ("可打印练习试卷",)
    assert "答案解析" in facts.benefits
    assert facts.blocked_terms == ()


def test_short_source_terms_do_not_gain_unconfirmed_meaning() -> None:
    facts = extract_title_facts(("五年级数学期末试卷知识解析",))

    assert facts.scenarios == ("期末",)
    assert facts.formats == ("试卷",)
    assert facts.benefits == ("解析",)
    assert "可打印" not in "".join((*facts.formats, *facts.benefits))
    assert "答案" not in "".join((*facts.formats, *facts.benefits))


def test_explicit_long_format_and_benefit_phrases_are_preserved() -> None:
    facts = extract_title_facts(("五年级数学专项练习 可打印文档 配套音频",))

    assert facts.formats == ("专项练习",)
    assert facts.benefits == ("可打印文档", "配套音频")


def test_explicit_group_dominance_removes_only_declared_redundancy() -> None:
    facts = extract_title_facts(("五年级数学课程音频 配套音频 普通试卷 可打印试卷 可打印 5题15题",))

    assert facts.formats == ("可打印试卷",)
    assert facts.benefits == ("配套音频", "可打印")
    assert facts.quantities == ("5题", "15题")
    candidate = _build_candidate(facts, "search")
    assert "音频配套音频" not in candidate.title
    assert candidate.benefits == ("配套音频", "可打印")
    assert candidate.title.count("可打印") == 1


def test_format_covered_printable_benefit_is_scored_without_duplicate_text() -> None:
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), formats=("可打印试卷",), benefits=("可打印",),
    )
    candidate = _build_candidate(facts, "search")
    score, reasons = _score(candidate.title, facts, "search", candidate.quantities, candidate.benefits)

    assert candidate.title.count("可打印") == 1
    assert candidate.benefits == ("可打印",)
    assert score == 83
    assert "包含经验证利益点" in reasons


def test_covered_printable_benefit_has_zero_display_cost_and_releases_budget() -> None:
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",),
        formats=("可打印试卷", "专题讲义", "视频课程", "知识清单", "专项练习", "学习资料包"),
        quantities=("10讲", "28专题", "180节", "1000词", "20套", "9科", "18册"),
        benefits=("可打印",),
    )
    without_benefit = TitleFacts(
        source_texts=facts.source_texts, grades=facts.grades, subjects=facts.subjects,
        scenarios=facts.scenarios, formats=facts.formats, quantities=facts.quantities,
    )

    candidate = _build_candidate(facts, "value")
    baseline = _build_candidate(without_benefit, "value")
    score, reasons = _score(candidate.title, facts, "value", candidate.quantities, candidate.benefits)
    baseline_score, _ = _score(
        baseline.title, without_benefit, "value", baseline.quantities, baseline.benefits
    )

    assert len(candidate.title) == 58
    assert candidate.title.count("可打印") == 1
    assert "9科" in candidate.title
    assert "18册" not in candidate.title
    assert MAX_TITLE_LENGTH - len(candidate.title) < len("18册")
    assert candidate.benefits == ("可打印",)
    assert score == baseline_score + 2
    assert "包含经验证利益点" in reasons


def test_printable_benefit_is_displayed_when_its_covering_format_does_not_fit() -> None:
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), formats=("可打印试卷",),
        quantities=("1000词", "10000词", "100000词", "1000000词", "10000000词", "1000000000词"),
        benefits=("可打印",),
    )

    candidate = _build_candidate(facts, "search")

    assert len(candidate.title) == MAX_TITLE_LENGTH
    assert "可打印试卷" not in candidate.title
    assert candidate.title.endswith("可打印")
    assert candidate.benefits == ("可打印",)


@pytest.mark.parametrize(
    ("source", "forbidden"),
    [("暑假资料", "预习"), ("课程音频", "配套")],
)
def test_ambiguous_source_terms_do_not_expand_facts(source: str, forbidden: str) -> None:
    facts = extract_title_facts((source,))
    assert forbidden not in "".join((*facts.scenarios, *facts.benefits))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("一年级至六年级数学", ("一至六年级",)),
        ("一年级到六年级数学", ("一至六年级",)),
        ("一年级—六年级数学", ("一至六年级",)),
        ("高一至高三数学", ("高一至高三",)),
        ("初一数学", ("七年级",)),
        ("初一至初三数学", ("七至九年级",)),
    ],
)
def test_chinese_grade_ranges_and_middle_school_aliases(source: str, expected: tuple[str, ...]) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("一到六年级数学", ("一至六年级",)),
        ("一～六年级数学", ("一至六年级",)),
        ("一-六年级数学", ("一至六年级",)),
        ("初一至三数学", ("七至九年级",)),
        ("初一到三年级数学", ("七至九年级",)),
        ("初一～三数学", ("七至九年级",)),
        ("高一至三数学", ("高一至高三",)),
        ("高一到三年级数学", ("高一至高三",)),
        ("高一～三数学", ("高一至高三",)),
    ],
)
def test_elliptical_chinese_grade_ranges_are_normalized_as_complete_facts(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    "source",
    [
        "六到一年级数学",
        "一至十年级数学",
        "初三至一数学",
        "初一至四年级数学",
        "高三至一数学",
        "高一至四年级数学",
    ],
)
def test_invalid_elliptical_chinese_ranges_are_blocked_without_endpoint_fallback(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    [
        "6到1到3年级数学",
        "六到一到三年级数学",
        "高三至一到二年级数学",
        "初一至三到二年级数学",
    ],
)
def test_chained_grade_ranges_are_protected_as_a_single_invalid_sample(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    [
        "1年级至2年级至3年级数学",
        "一年级至二年级至三年级数学",
        "初一年级至初二年级至初三年级数学",
        "高一年级至高二年级至高三年级数学",
        "6到一年级数学",
        "六到1到三年级数学",
        "6到一到3年级数学",
    ],
)
def test_unified_grade_range_parser_blocks_mixed_and_chained_samples(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    [
        "1至10年级数学",
        "10至1年级数学",
        "1至2至10年级数学",
        "10至2至3年级数学",
        "一至十年级数学",
        "十至一年级数学",
        "一至二至十年级数学",
    ],
)
def test_multidigit_range_endpoints_are_invalid_as_complete_tokens(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    [
        "11年级至1年级数学",
        "100年级至1年级数学",
        "11至2至3年级数学",
        "一至十年级数学",
        "一至二至十年级数学",
        "一至二十年级数学",
        "一至至二年级数学",
        "一--二年级数学",
        "一～～二年级数学",
    ],
)
def test_malformed_or_multidigit_range_samples_never_leak_grade_endpoints(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    ["1000至1年级数学", "9999至1数学", "资料11至1数学", "资料100至1数学"],
)
def test_any_position_or_length_ascii_range_endpoint_is_protected(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("一至六可打印试卷", ("一至六年级",)),
        ("一至六资料包", ("一至六年级",)),
        ("一至六答案解析", ("一至六年级",)),
        ("一至六100题", ("一至六年级",)),
    ],
)
def test_bare_ranges_accept_all_verified_fact_boundaries(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("五年级-数学", ("五年级",)),
        ("五年级-可打印", ("五年级",)),
        ("五年级-期末", ("五年级",)),
        ("五年级，数学", ("五年级",)),
        ("五年级、数学", ("五年级",)),
        ("五年级/数学", ("五年级",)),
        ("高一，英语", ("高一",)),
        ("初一/数学", ("七年级",)),
    ],
)
def test_plain_title_separators_do_not_discard_a_valid_left_grade(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize("source", ["五年级--三年级数学", "初一～～三数学"])
def test_repeated_range_connector_with_an_endpoint_remains_a_protected_sample(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected_grade", "expected_quantity"),
    [
        ("五年级-100题", ("五年级",), ("100题",)),
        ("五年级-20套", ("五年级",), ("20套",)),
        ("初一-28专题", ("七年级",), ("28专题",)),
        ("高一-180节", ("高一",), ("180节",)),
    ],
)
def test_grade_to_quantity_separator_is_not_consumed_as_a_range(
    source: str, expected_grade: tuple[str, ...], expected_quantity: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == expected_grade
    assert facts.quantities == expected_quantity


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("一年级至六年级100题", ("一至六年级",)),
        ("高一至高三20套", ("高一至高三",)),
        ("初一至初三可打印试卷", ("七至九年级",)),
        ("一年级至六年级资料包", ("一至六年级",)),
    ],
)
def test_explicit_right_grade_suffix_allows_following_real_facts(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    "source",
    ["十一年级数学", "十二年级数学", "二十一年级数学", "10年级数学", "11年级数学", "初一十一年级数学"],
)
def test_invalid_single_grade_tokens_are_kept_indivisible(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize("source", ["十一年级-数学", "高四至期末", "初四～可打印"])
def test_invalid_single_grade_token_with_non_grade_tail_remains_protected(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize("source", ["1、2年级数学", "1,2年级数学", "1/2年级数学", "初一、二年级数学"])
def test_grade_lists_are_protected_when_list_semantics_are_not_confirmed(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("五年级、六年级数学", ("五年级", "六年级")),
        ("5年级,6年级数学", ("五年级", "六年级")),
        ("初一、初二、初三数学", ("七年级", "八年级", "九年级")),
        ("高一/高二/高三数学", ("高一", "高二", "高三")),
        ("五年级、六年级、七年级数学", ("五年级", "六年级", "七年级")),
    ],
)
def test_explicit_homogeneous_grade_lists_keep_every_endpoint(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize("source", ["1、2年级、3年级数学", "初一、二年级、初三数学"])
def test_ambiguous_grade_list_is_protected_through_its_last_endpoint(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    ["1、2年级、数学", "1,2年级,数学", "初一、二年级/数学"],
)
def test_ambiguous_grade_list_stops_before_ordinary_tail_separator_without_leaking(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("5年级、六年级数学", ("五年级", "六年级")),
        ("五年级、6年级数学", ("五年级", "六年级")),
    ],
)
def test_explicit_primary_lists_allow_ascii_and_chinese_endpoint_spellings(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


def test_grade_list_parser_reads_long_lists_linearly(monkeypatch: pytest.MonkeyPatch) -> None:
    original = title_strategy._read_grade_endpoint
    calls: list[int] = []

    for count in (400, 800, 1600):
        observed = 0

        def counted(value: str, start: int) -> tuple[str, int, bool, int] | None:
            nonlocal observed
            observed += 1
            return original(value, start)

        monkeypatch.setattr(title_strategy, "_read_grade_endpoint", counted)
        source = "、".join(f"{index % 9 + 1}年级" for index in range(count))
        extract_title_facts((source,))
        calls.append(observed)

    assert calls[1] <= calls[0] * 3
    assert calls[2] <= calls[0] * 5


@pytest.mark.parametrize(
    ("source", "expected_grades", "expected_quantities"),
    [
        ("五年级、100题", ("五年级",), ("100题",)),
        ("五、六年级、100题", ("五年级", "六年级"), ("100题",)),
        ("初一、初二、28专题", ("七年级", "八年级"), ("28专题",)),
        ("高一/高二/180节", ("高一", "高二"), ("180节",)),
    ],
)
def test_grade_list_stops_before_a_following_quantity_atom(
    source: str, expected_grades: tuple[str, ...], expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == expected_grades
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("六年级、初一", ("六年级", "七年级")),
        ("九年级、高一", ("九年级", "高一")),
        ("初三、高一", ("九年级", "高一")),
        ("五年级、初二、高三", ("五年级", "八年级", "高三")),
    ],
)
def test_explicit_cross_system_grade_lists_keep_every_confirmed_endpoint(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("初一、六年级", ("七年级", "六年级")),
        ("初一年级、六年级", ("七年级", "六年级")),
        ("高一、九年级", ("高一", "九年级")),
        ("九年级、初一", ("九年级", "七年级")),
    ],
)
def test_cross_system_explicit_lists_are_direction_independent(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    "source",
    [
        "课程10-10年级、五年级、六年级",
        "2026-高四、初一、初二",
        "课程10-10年级",
        "2026-高四",
    ],
)
def test_labelled_invalid_first_grade_construct_protects_its_entire_list(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    [
        "六年级、初一、二年级",
        "九年级、高一、二年级",
        "五年级、初二、三年级",
    ],
)
def test_marker_inheritance_ambiguity_is_checked_against_each_previous_endpoint(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    ["高一百", "高一百年级", "高二十", "高二十年级", "初一百", "初一百年级", "初二十", "初二十年级"],
)
def test_invalid_compound_middle_and_high_tokens_are_protected_whole(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize("source", ["课程10-十、五年级、六年级", "2026-十、五年级"])
def test_labelled_bare_invalid_chinese_grade_starts_protect_following_list(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected_quantities"),
    [
        ("课程10-10、五年级、六年级", ()),
        ("2026-99、五年级、100题", ("100题",)),
    ],
)
def test_labelled_bare_invalid_ascii_grade_starts_protect_following_list(
    source: str, expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == ()
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected_grades", "expected_quantities"),
    [
        ("四、五、六年级", ("四年级", "五年级", "六年级"), ()),
        ("四、五、六年级、100题", ("四年级", "五年级", "六年级"), ("100题",)),
    ],
)
def test_chinese_primary_list_allows_three_or_more_tail_suffix_omissions(
    source: str, expected_grades: tuple[str, ...], expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == expected_grades
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected_quantities"),
    [
        ("四、五、六、四、五、100题", ("100题",)),
        ("四、五、高一", ()),
        ("四、五、六、七、八、九、100题", ("100题",)),
    ],
)
def test_chinese_primary_abbreviated_lists_require_a_terminal_grade_suffix_anchor(
    source: str, expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == ()
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected_grades", "expected_quantities"),
    [
        ("四 、 五 、 六年级", ("四年级", "五年级", "六年级"), ()),
        ("四、五年级、高一", ("四年级", "五年级", "高一"), ()),
        (
            "四、五、六年级、高一、100题",
            ("四年级", "五年级", "六年级", "高一"),
            ("100题",),
        ),
        (
            "四、五年级、高一、六年级",
            ("四年级", "五年级", "高一", "六年级"),
            (),
        ),
    ],
)
def test_grade_list_scanner_expands_only_the_anchored_primary_segment(
    source: str, expected_grades: tuple[str, ...], expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == expected_grades
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    "source",
    ["课程10-99、、五年级、六年级", "2026-99//五年级"],
)
def test_invalid_labelled_grade_lists_consume_repeated_list_separators(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected_quantities"),
    [
        ("四、十年级、六年级、100题", ("100题",)),
        ("四、10年级、六年级", ()),
        ("四、、五年级、六年级", ()),
        ("四、初一、二年级、六年级", ()),
        ("四、高一、二年级、高三", ()),
    ],
)
def test_ambiguous_chinese_shorthand_list_consumes_through_its_true_end(
    source: str, expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == ()
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected_grades", "expected_quantities"),
    [
        ("四年级、初一至初三", ("四年级", "七至九年级"), ()),
        (
            "四年级、一至二年级、高一至高三、七年级",
            ("四年级", "一至二年级", "高一至高三", "七年级"),
            (),
        ),
    ],
)
def test_grade_lists_keep_complete_range_atoms_as_single_facts(
    source: str, expected_grades: tuple[str, ...], expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == expected_grades
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected_quantities"),
    [
        ("课程10-99、五至六年级、100题", ("100题",)),
        ("四、初一、二年级、六至九年级、100题", ("100题",)),
        ("四年级、六至一年级", ()),
    ],
)
def test_invalid_or_ambiguous_range_atoms_protect_the_entire_list(
    source: str, expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == ()
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("高一、一至二年级", ("高一", "一至二年级")),
        ("初一、一至二年级", ("七年级", "一至二年级")),
    ],
)
def test_marker_inheritance_does_not_reject_a_complete_range_atom(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    "source",
    [
        "六至一年级、五年级",
        "课程10-六至一年级、五年级",
        "一至二年级、10年级、五年级",
        "一至二年级、初一、二年级",
    ],
)
def test_range_as_first_list_atom_uses_the_same_whole_list_protection(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("一至二年级、五年级、高一至高二", ("一至二年级", "五年级", "高一至高二")),
        ("四年级、五年级-数学", ("四年级", "五年级")),
        ("四年级、一至二年级-数学", ("四年级", "一至二年级")),
        ("四年级、初一至初三-英语", ("四年级", "七至九年级")),
    ],
)
def test_valid_list_ranges_and_trailing_title_separators_keep_verified_atoms(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected_grades"),
    [
        ("四年级、一年级-三套", ("四年级", "一年级")),
        ("四年级、高一-三套", ("四年级", "高一")),
        ("四年级、初一-三科", ("四年级", "七年级")),
        ("四年级、一至二年级-三套", ("四年级", "一至二年级")),
    ],
)
def test_list_range_atoms_reuse_the_top_level_quantity_boundary(
    source: str, expected_grades: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected_grades


@pytest.mark.parametrize(
    "source",
    ["四至、六年级", "四年级、五至、六年级", "五--、六年级"],
)
def test_dangling_range_connectors_before_a_list_separator_protect_the_list(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    "source",
    ["高一、二至三数学", "初一、二至三英语"],
)
def test_bare_primary_range_after_marker_is_an_inheritance_ambiguity(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("高一、二至三年级", ("高一", "二至三年级")),
        ("初一、高二至高三", ("七年级", "高二至高三")),
    ],
)
def test_explicitly_anchored_ranges_after_markers_remain_verified(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    "source",
    [
        "高一、高二至三、二年级",
        "初一、初二至三、二年级",
        "高一至三、二至三数学",
    ],
)
def test_marker_ranges_keep_their_semantic_terminal_for_following_ambiguity(
    source: str,
) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("高一、高二至三、四年级", ("高一", "高二至高三", "四年级")),
        ("初一、初二至三、四至五年级", ("七年级", "八至九年级", "四至五年级")),
        ("高一至三、二至三年级", ("高一至高三", "二至三年级")),
    ],
)
def test_marker_ranges_keep_explicit_following_atoms_verified(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize("source", ["10年级、五年级", "十一年级、五年级", "高四、初一", "初四、五年级"])
def test_invalid_first_grade_list_endpoint_protects_the_entire_list(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


def test_course_number_dispatches_ambiguous_grade_list_to_the_same_protection() -> None:
    assert extract_title_facts(("课程10-1、2年级数学",)).grades == ()


def test_ordered_matches_scales_without_quadratic_occupied_span_scans() -> None:
    durations: list[float] = []
    terms = ("资料资料", "资料资", "料资料", "资料")
    for size in (400, 800, 1600):
        text = "资料" * size
        started = perf_counter()
        for _ in range(3):
            _ordered_matches((text,), terms)
        durations.append(perf_counter() - started)

    assert durations[1] <= durations[0] * 5 + 0.01
    assert durations[2] <= durations[0] * 9 + 0.02


@pytest.mark.parametrize("source", ["资料2026-5年级数学", "课程2026-5年级数学", "（2026-5年级）数学"])
def test_year_label_before_single_grade_is_not_treated_as_a_range(source: str) -> None:
    assert extract_title_facts((source,)).grades == ("五年级",)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("2026-五年级数学", ("五年级",)),
        ("2026—初一数学", ("七年级",)),
        ("2026/高一数学", ("高一",)),
        ("2026，一至六年级数学", ("一至六年级",)),
        ("课程10-5年级数学", ("五年级",)),
    ],
)
def test_number_labels_allow_explicit_k12_single_or_range_after_common_separator(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("课程 10-5年级数学", ("五年级",)),
        ("数学课程 10-5年级", ("五年级",)),
        ("课程编号10—5年级", ("五年级",)),
        ("课程 10 — 5年级", ("五年级",)),
        ("课程10-1至6年级数学", ("一至六年级",)),
        ("2026、5年级数学", ("五年级",)),
        ("2026 ， 5年级数学", ("五年级",)),
        ("2026 / 初一数学", ("七年级",)),
        ("2026 — 一至六年级数学", ("一至六年级",)),
    ],
)
def test_course_and_year_label_grammars_allow_spaces_separators_and_ranges(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected_grades", "expected_quantities"),
    [
        ("资料11-5年级数学", (), ()),
        ("1-6套试卷", (), ("6套",)),
        ("一至六全100题数学", ("一至六年级",), ("100题",)),
        ("五年级--100题", ("五年级",), ("100题",)),
        ("高一～～3科", ("高一",), ("3科",)),
    ],
)
def test_range_scanner_distinguishes_quantity_atoms_from_grade_endpoints(
    source: str, expected_grades: tuple[str, ...], expected_quantities: tuple[str, ...]
) -> None:
    facts = extract_title_facts((source,))
    assert facts.grades == expected_grades
    assert facts.quantities == expected_quantities


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("一～六数学", ("一至六年级",)),
        ("一-六，资料", ("一至六年级",)),
        ("一至六期末", ("一至六年级",)),
        ("一年级-三套", ("一年级",)),
        ("一年级-三册", ("一年级",)),
        ("一年级-三题", ("一年级",)),
        ("高一-三套", ("高一",)),
        ("初一-三科", ("七年级",)),
    ],
)
def test_bare_grade_ranges_require_a_clear_boundary_and_never_cross_quantities(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("初一至初二数学", ("七至八年级",)),
        ("初二到初三数学", ("八至九年级",)),
        ("初一—初三数学", ("七至九年级",)),
        ("一年级至十年级数学", ()),
        ("高一至高四数学", ()),
        ("初一至初四数学", ()),
        ("初三至初一数学", ()),
    ],
)
def test_chinese_composite_ranges_preserve_real_endpoints_or_block_invalid_ones(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    "source",
    ["高一至高五数学", "初一至初五数学", "一年级至十一年级数学", "十一年级至六年级数学", "一百年级至二百年级数学", "一年级至一千年级数学", "一千年级至六年级数学", "一万年级至六年级数学", "一亿年级至六年级数学"],
)
def test_unparseable_chinese_range_endpoints_are_blocked_as_a_whole(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("5年级数学期末资料", ("五年级",)),
        ("1-6年级数学期末资料", ("一至六年级",)),
        ("1—6年级数学期末资料", ("一至六年级",)),
        ("1–6年级数学期末资料", ("一至六年级",)),
        ("7-9年级英语期末资料", ("七至九年级",)),
    ],
)
def test_extract_title_facts_normalizes_numeric_grades(source: str, expected: tuple[str, ...]) -> None:
    facts = extract_title_facts((source,))

    assert facts.grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1至6年级数学", ("一至六年级",)),
        ("1 至 6年级数学", ("一至六年级",)),
        ("7到9年级英语", ("七至九年级",)),
        ("1~6年级数学", ("一至六年级",)),
        ("1～6年级数学", ("一至六年级",)),
        ("1至5年级数学", ("一至五年级",)),
        ("6 到 1年级数学", ()),
        ("1、2年级数学", ()),
    ],
)
def test_numeric_grade_ranges_support_common_separators_without_fallback(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize("source", ["2026 5年级数学期末资料", "课程1 5年级数学"])
def test_spaced_numbers_do_not_block_a_real_single_numeric_grade(source: str) -> None:
    assert extract_title_facts((source,)).grades == ("五年级",)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("2026-5年级数学", ("五年级",)),
        ("2026，5年级数学", ("五年级",)),
        ("2026/5年级数学", ("五年级",)),
        ("课程10-5年级数学", ("五年级",)),
        ("1-5年级数学", ("一至五年级",)),
        ("6到1年级数学", ()),
        ("1、2年级数学", ()),
        ("1至10年级数学", ()),
    ],
)
def test_numeric_grade_composites_require_an_independent_k12_start(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("2-5年级数学", ("二至五年级",)),
        ("3至6年级语文", ("三至六年级",)),
        ("8-9年级英语", ("八至九年级",)),
        ("小学1-5年级数学", ("一至五年级",)),
    ],
)
def test_all_legal_numeric_ranges_are_complete_grade_facts(
    source: str, expected: tuple[str, ...]
) -> None:
    assert extract_title_facts((source,)).grades == expected


@pytest.mark.parametrize("source", ["0年级数学", "10年级数学", "6-1年级数学", "9-7年级英语"])
def test_extract_title_facts_rejects_invalid_numeric_grade_boundaries(source: str) -> None:
    assert extract_title_facts((source,)).grades == ()


def test_numeric_grade_titles_use_canonical_chinese_prefix() -> None:
    facts = extract_title_facts(("5年级数学期末资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",))

    assert select_best_title(facts).title.startswith("五年级数学")


@pytest.mark.parametrize("value", ["\n", "\u2028", "\x1c"])
def test_extract_title_facts_does_not_silently_skip_forbidden_empty_controls(value: str) -> None:
    with pytest.raises(TitlePolicyError) as error:
        extract_title_facts((value,))

    assert error.value.code == "TITLE_INVALID"


def test_extract_title_facts_skips_only_ordinary_unicode_space_and_tabs() -> None:
    assert extract_title_facts((" \u3000\t", "五年级数学期末资料")).grades == ("五年级",)


def test_extract_title_facts_prefers_specific_answer_analysis_over_overlap() -> None:
    facts = extract_title_facts(("五年级数学含答案解析资料",))

    assert facts.benefits == ("答案解析",)


def test_ordered_matches_prefers_a_later_longer_overlapping_term() -> None:
    assert _ordered_matches(("甲乙丙丁",), ("甲乙", "乙丙丁")) == ("乙丙丁",)


def test_extract_title_facts_keeps_only_explicit_general_high_school_grade() -> None:
    facts = extract_title_facts(("高中数学学习资料包", "专题讲义与练习"))

    assert facts.grades == ("高中",)
    assert not {"高一", "高二", "高三"}.intersection(facts.grades)
    assert facts.subjects == ("数学",)
    assert facts.formats == ("学习资料包", "专题讲义", "练习")


def test_extract_title_facts_does_not_split_explicit_multi_grade_range() -> None:
    facts = extract_title_facts(("高一高二高三数学一轮复习资料",))

    assert facts.grades == ("高一高二高三",)
    assert facts.subjects == ("数学",)
    assert facts.scenarios == ("一轮复习",)


@pytest.mark.parametrize("quantity", ["100讲", "28专题", "180节", "9科", "全18册"])
def test_extract_title_facts_collects_verified_quantities(quantity: str) -> None:
    facts = extract_title_facts((f"五年级数学{quantity}期末资料",))

    assert quantity.removeprefix("全") in facts.quantities


def test_extract_title_facts_preserves_new_quantity_units_in_source_order() -> None:
    facts = extract_title_facts(("高中英语3500词500题20套",))

    assert facts.quantities == ("3500词", "500题", "20套")


def test_select_best_title_generates_and_automatically_chooses_three_candidates() -> None:
    facts = extract_title_facts(
        (
            "五年级数学期末资料 28专题 可打印练习试卷",
            "含答案解析 100讲知识清单",
        )
    )

    decision = select_best_title(facts)

    assert len(decision.candidates) == 3
    assert decision.title.startswith("五年级数学")
    assert MIN_TITLE_LENGTH <= len(decision.title) <= MAX_TITLE_LENGTH
    assert decision.title == max(
        decision.candidates, key=lambda candidate: candidate.score
    ).title
    assert decision.selected_kind == max(
        decision.candidates, key=lambda candidate: candidate.score
    ).kind


def test_select_best_title_generates_three_unique_titles_without_quantities() -> None:
    facts = extract_title_facts(
        ("五年级数学期末资料 28专题 可打印练习试卷 含答案解析 配套音频 知识清单 专题讲义 视频课程",)
    )

    decision = select_best_title(facts)

    assert len(decision.candidates) == 3
    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)
    titles_by_kind = {candidate.kind: candidate.title for candidate in decision.candidates}
    assert all(candidate.title for candidate in decision.candidates)


def test_select_best_title_keeps_grade_subject_early_and_scenario_within_thirty() -> None:
    facts = extract_title_facts(
        ("七年级英语暑假预习资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",)
    )

    decision = select_best_title(facts)

    assert decision.title.startswith("七年级英语")
    assert decision.title.index("暑假预习") < 30
    assert len("七年级英语") <= 15


def test_non_review_scenarios_do_not_add_a_review_tail() -> None:
    facts = extract_title_facts(
        ("七年级英语暑假预习资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",)
    )

    decision = select_best_title(facts)

    assert all("复习" not in candidate.title for candidate in decision.candidates)


def test_select_best_title_reports_insufficient_when_all_three_attempts_are_invalid() -> None:
    facts = extract_title_facts(("高中资料",))

    with pytest.raises(TitlePolicyError) as error:
        select_best_title(facts)

    assert error.value.code == "TITLE_INFORMATION_INSUFFICIENT"
    assert "insufficient" in str(error.value)


def test_select_best_title_structurally_distinguishes_three_quantity_only_candidates() -> None:
    facts = extract_title_facts(("五年级数学期末专项复习100题20套30讲40册50专题60节",))

    decision = select_best_title(facts)

    assert tuple(candidate.kind for candidate in decision.candidates) == ("search", "scenario", "value")
    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)
    assert decision.selected_kind == "search"
    assert decision.title == decision.candidates[0].title


def test_candidate_fallback_recovers_three_long_quantity_variants_from_short_greedy_attempts() -> None:
    quantity = "1" * 44 + "题"
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), quantities=(quantity,),
        formats=("可打印试卷",), benefits=("答案解析",),
    )

    decision = select_best_title(facts)
    titles = {candidate.kind: candidate.title for candidate in decision.candidates}

    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)
    assert all(quantity in title for title in titles.values())
    assert "期末专项复习" not in titles["scenario"]
    assert titles["value"].endswith("期末专项复习")


def test_candidate_layout_search_can_omit_or_move_the_primary_scenario_for_a_long_quantity() -> None:
    quantity = "1" * 46 + "题"
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), quantities=(quantity,),
    )

    decision = select_best_title(facts)
    titles = {candidate.kind: candidate.title for candidate in decision.candidates}

    assert titles["search"] == f"五年级数学期末专项复习{quantity}"
    assert titles["scenario"] == f"五年级数学{quantity}"
    assert titles["value"] == f"五年级数学{quantity}期末专项复习"
    assert all(MIN_TITLE_LENGTH <= len(title) <= MAX_TITLE_LENGTH for title in titles.values())


@pytest.mark.parametrize("digits", [45, 46, 47, 48, 49, 50])
def test_candidate_search_uses_distinct_verified_subsets_near_the_length_boundary(digits: int) -> None:
    quantity = "1" * digits + "题"
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), quantities=(quantity,),
        formats=("可打印试卷",), benefits=("答案解析",),
    )

    decision = select_best_title(facts)

    assert len(decision.candidates) == 3
    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)
    assert all(quantity in candidate.title for candidate in decision.candidates)


@pytest.mark.parametrize(
    ("facts", "boundary"),
    [
        (
            TitleFacts(
                source_texts=("已验证",), grades=("高一",), subjects=("数学",),
                scenarios=("期末",), quantities=("1" * 23 + "题",), benefits=("解析",),
            ),
            MIN_TITLE_LENGTH,
        ),
        (
            TitleFacts(
                source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
                scenarios=("期末专项复习",), quantities=("1" * 48 + "题",), benefits=("答案解析",),
            ),
            MAX_TITLE_LENGTH,
        ),
    ],
)
def test_candidate_search_preserves_exact_thirty_and_sixty_character_boundaries(
    facts: TitleFacts, boundary: int,
) -> None:
    decision = select_best_title(facts)

    lengths = {len(candidate.title) for candidate in decision.candidates}

    assert len(decision.candidates) == 3
    assert MIN_TITLE_LENGTH <= min(lengths) <= max(lengths) <= MAX_TITLE_LENGTH
    assert boundary in lengths


def test_structural_layout_fallback_respects_the_sixty_character_budget() -> None:
    first_quantity = "1234567890123456789012题"
    second_quantity = "9876543210987654321098套"
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), quantities=(first_quantity, second_quantity),
    )

    decision = select_best_title(facts)
    titles = {candidate.kind: candidate.title for candidate in decision.candidates}

    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)
    assert "期末专项复习" not in titles["scenario"]
    assert titles["value"].endswith("期末专项复习")
    assert all(quantity in title for quantity in facts.quantities for title in titles.values())


def test_select_best_title_prunes_long_facts_by_atomic_priority() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("五年级",),
        subjects=("数学",),
        scenarios=("期末专项复习", "同步学习", "开学衔接"),
        formats=("可打印练习试卷", "专题讲义", "视频课程", "知识清单", "专项练习", "学习资料包"),
        quantities=("100讲", "28专题", "180节", "9科", "18册", "500题", "20套"),
        benefits=("答案解析", "可打印文档", "配套音频", "含答案"),
    )

    decision = select_best_title(facts)

    assert decision.candidates
    assert any(
        candidate.title.startswith("五年级数学")
        and "期末专项复习" in candidate.title
        and MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH
        for candidate in decision.candidates
    )


def test_long_rich_facts_keep_three_unique_candidates_without_tails() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("高一高二高三",),
        subjects=("全科",),
        scenarios=("期末专项复习",),
        formats=("可打印练习试卷", "专题讲义", "视频课程", "知识清单", "专项练习", "学习资料包"),
        quantities=("100讲", "28专题", "180节", "9科", "18册", "500题", "20套"),
        benefits=("答案解析", "可打印文档", "配套音频", "含答案"),
    )

    decision = select_best_title(facts)

    assert len(decision.candidates) == 3
    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)


def test_long_rich_facts_without_quantities_keep_three_unique_candidates() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("高一高二高三",),
        subjects=("全科",),
        scenarios=("期末专项复习",),
        formats=("可打印练习试卷", "专题讲义", "视频课程", "知识清单", "专项练习", "学习资料包"),
        benefits=("答案解析", "可打印文档", "配套音频", "含答案"),
    )

    decision = select_best_title(facts)

    assert len(decision.candidates) == 3
    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)


def test_long_quantity_only_facts_use_stable_verified_fact_order_for_unique_candidates() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("高一高二高三",),
        subjects=("全科",),
        scenarios=("暑假预习", "同步学习"),
        quantities=("100讲", "28专题", "180节", "9科", "18册", "500题", "20套", "3500词"),
        benefits=("答案解析",),
    )

    decision = select_best_title(facts)

    assert tuple(candidate.kind for candidate in decision.candidates) == ("search", "scenario", "value")
    assert len({candidate.title for candidate in decision.candidates}) == 3
    assert all(MIN_TITLE_LENGTH <= len(candidate.title) <= MAX_TITLE_LENGTH for candidate in decision.candidates)


def test_score_counts_only_quantities_and_benefits_present_in_pruned_title() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("五年级",),
        subjects=("数学",),
        scenarios=("期末专项复习",),
        quantities=("100讲", "28专题", "180节", "9科", "18册", "500题", "20套"),
        benefits=("答案解析", "可打印文档", "配套音频", "含答案"),
    )
    title = "五年级数学期末专项复习100讲答案解析可打印练习试卷学习资料"

    score, reasons = _score(title, facts, "search", ("100讲",), ("答案解析",))

    assert score > 81
    assert "包含经验证数量" in reasons
    assert "包含经验证利益点" in reasons


def test_score_uses_selected_atoms_not_overlapping_title_substrings() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("五年级",),
        subjects=("数学",),
        scenarios=("期末专项复习",),
        quantities=("5题", "15题"),
        benefits=("答案解析", "配套音频"),
    )
    title = "五年级数学期末专项复习15题答案解析可打印练习试卷学习资料"

    score, reasons = _score(title, facts, "search", ("15题",), ("答案解析",))

    assert score == 85
    assert "包含经验证数量" in reasons
    assert "包含经验证利益点" in reasons


def test_candidate_metadata_tracks_only_nonredundant_atoms() -> None:
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), quantities=("5题", "15题"),
        benefits=("音频", "配套音频"),
    )

    candidate = _build_candidate(facts, "value")
    score, reasons = _score(candidate.title, facts, "value", candidate.quantities, candidate.benefits)

    assert candidate.quantities == ("5题", "15题")
    assert candidate.benefits == ("音频", "配套音频")
    assert score == 88
    assert "包含经验证数量" in reasons
    assert "包含经验证利益点" in reasons


def test_deferred_printable_metadata_is_counted_once_for_multiple_covering_formats() -> None:
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",),
        formats=("可打印试卷", "可打印练习试卷"),
        quantities=("100题", "20套", "30讲"), benefits=("可打印",),
    )

    built = next(
        candidate
        for candidate in _bounded_factual_candidates(facts)
        if all(format_ in candidate.title for format_ in facts.formats)
    )
    score, reasons = _score(built.title, facts, "search", built.quantities, built.benefits)

    assert built.benefits == ("可打印",)
    assert score == 89
    assert reasons.count("包含经验证利益点") == 1


def test_quantity_atoms_with_overlapping_digits_are_all_retained_and_scored() -> None:
    facts = TitleFacts(
        source_texts=("已验证",), grades=("五年级",), subjects=("数学",),
        scenarios=("期末专项复习",), quantities=("5题", "15题", "20套", "120套"),
    )
    candidate = _build_candidate(facts, "search")
    score, reasons = _score(candidate.title, facts, "search", candidate.quantities, candidate.benefits)

    assert candidate.quantities == ("5题", "15题", "20套", "120套")
    assert all(quantity in candidate.title for quantity in candidate.quantities)
    assert score == 89
    assert "包含经验证数量" in reasons


@pytest.mark.parametrize("risk_term", ["人教版", "小状元", "学而思", "山东省", "某某学校"])
def test_risk_terms_are_removed_from_facts_candidates_and_final_title(risk_term: str) -> None:
    risk_terms = (*RISK_TERMS, risk_term)
    facts = extract_title_facts(
        (f"五年级数学{risk_term}期末资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",),
        risk_terms=risk_terms,
    )

    decision = select_best_title(facts)

    assert risk_term in facts.blocked_terms
    assert all(risk_term not in candidate.title for candidate in decision.candidates)
    assert risk_term not in decision.title


def test_extract_title_facts_records_overlapping_actual_risk_terms() -> None:
    facts = extract_title_facts(
        ("五年级数学某某学校期末资料",),
        risk_terms=(*RISK_TERMS, "某某学校"),
    )

    assert facts.blocked_terms == ("某某学校", "学校")


def test_risk_term_removal_keeps_neighboring_tokens_separate() -> None:
    facts = extract_title_facts(("初【学而思】中资料",))

    assert "初中" not in facts.source_texts[0]
    assert "初中" not in facts.grades


def test_custom_risk_policy_blocks_a_generated_real_fact() -> None:
    facts = extract_title_facts(
        ("五年级数学期末资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",),
        risk_terms=("知识清单",),
    )

    decision = select_best_title(facts)

    assert "知识清单" in facts.risk_terms
    assert facts.blocked_terms == ("知识清单",)
    assert all("知识清单" not in candidate.title for candidate in decision.candidates)
    assert "知识清单" not in decision.title


def test_all_generated_tail_risks_report_policy_failure() -> None:
    facts = extract_title_facts(
        ("五年级数学期末资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",),
        risk_terms=("期末", "可打印练习试卷", "答案解析"),
    )

    with pytest.raises(TitlePolicyError) as error:
        select_best_title(facts)

    assert error.value.code == "TITLE_INFORMATION_INSUFFICIENT"


def test_policy_failure_audit_includes_safe_length_context() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("五年级",),
        subjects=("数学" * 30,),
        scenarios=("期末专项复习",),
    )

    with pytest.raises(TitlePolicyError) as error:
        select_best_title(facts)

    assert error.value.code == "TITLE_POLICY_FAILED"
    assert "search:TITLE_LENGTH[length=" in str(error.value)


def test_risk_audit_prefers_longest_overlapping_term_deterministically() -> None:
    facts = extract_title_facts(
        ("五年级数学期末资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",),
        risk_terms=("期", "期末", "可打印练习试卷", "答案解析"),
    )

    with pytest.raises(TitlePolicyError) as error:
        select_best_title(facts)

    assert error.value.code == "TITLE_INFORMATION_INSUFFICIENT"


def test_overlong_policy_failures_are_not_reported_as_insufficient() -> None:
    facts = TitleFacts(
        source_texts=("已验证的丰富资料",),
        grades=("五年级",),
        subjects=("数学" * 30,),
        scenarios=("期末专项复习",),
    )

    with pytest.raises(TitlePolicyError) as error:
        select_best_title(facts)

    assert error.value.code == "TITLE_POLICY_FAILED"
    assert "TITLE_LENGTH" in str(error.value)


def test_select_best_title_breaks_equal_scores_by_original_candidate_order(monkeypatch) -> None:
    facts = extract_title_facts(
        ("五年级数学期末资料 28专题 可打印练习试卷 含答案解析 知识清单 专题讲义 视频课程 配套音频",)
    )
    import windows_capabilities.wechat_title_strategy as strategy

    monkeypatch.setattr(strategy, "_score", lambda *args: (50, ("tie",)))

    decision = strategy.select_best_title(facts)

    assert decision.selected_kind == decision.candidates[0].kind
    assert decision.title == decision.candidates[0].title
