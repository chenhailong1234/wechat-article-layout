import pytest

from windows_capabilities.wechat_resource_pipeline.policy import (
    forbidden_publication_terms,
    sanitize_publication_copy,
    sanitize_visible_names,
    validate_publication_html,
)


def test_sanitize_publication_copy_removes_risks_and_normalizes_wording() -> None:
    assert sanitize_publication_copy("  五年级 人教版【黄冈】数学课程训练营资料  ，精品  ") == "五年级 数学资料，精品"


def test_forbidden_publication_terms_reports_each_policy_category_once() -> None:
    assert forbidden_publication_terms("人教课程，更多资源，人教") == ("人教", "更多资源", "课程")


def test_sanitize_visible_names_drops_empty_and_duplicate_names() -> None:
    assert sanitize_visible_names(("五年级人教版数学课程", "更多资源", "五年级人教版数学课程", "  ")) == ("五年级 数学资料",)


@pytest.mark.parametrize("term", ["学而思", "训练营", "waterinbullrun.com"])
def test_validate_publication_html_rejects_forbidden_copy(term: str) -> None:
    with pytest.raises(ValueError, match="forbidden publication term"):
        validate_publication_html(f"<p>{term}</p>")


def test_validate_publication_html_accepts_sanitized_copy() -> None:
    validate_publication_html("<p>五年级数学资料</p>")
