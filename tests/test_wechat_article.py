import base64
from dataclasses import FrozenInstanceError
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from windows_capabilities.wechat_article import (
    MAX_SOURCE_IMAGE_BYTES,
    WechatArticleInput,
    _compressed_jpeg,
    build_wechat_article,
)
from windows_capabilities.wechat_title_strategy import TitlePolicyError
from windows_capabilities.wechat_title_strategy import extract_title_facts, select_best_title


VALID_WECHAT_TITLE = "五年级数学期末专项复习资料<精选>可打印练习试卷及答案解析完整合集"
LONG_WECHAT_TITLE = VALID_WECHAT_TITLE + "知识清单同步训练重点难点梳理课后巩固练习精选汇编真题精编"
RISK_WECHAT_TITLE = "五年级数学人教版期末专项复习资料可打印练习试卷答案解析完整合集"
MISPLACED_PREFIX_TITLE = "期末资料五年级数学可打印练习试卷及答案解析完整合集知识清单重点"


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-wechat-summary" in values or "data-content-source-url" in values:
            self.metadata.append(values)


def make_file(path: Path, image_format: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image_format = image_format or ("JPEG" if path.suffix.lower() in {".jpg", ".jpeg"} else "PNG")
    color = {
        "directory": "red",
        "content-1": "green",
        "content-2": "blue",
        "content-3": "purple",
        "cover": "yellow",
    }.get(path.stem, "orange")
    Image.new("RGB", (2, 2), color).save(path, format=image_format)
    return path


def article(tmp_path: Path, content_count: int = 2, **changes) -> WechatArticleInput:
    values = {
        "title": VALID_WECHAT_TITLE,
        "description": "覆盖一至六年级 & 配套答案",
        "new_link": "https://pan.quark.cn/s/Abc123x",
        "keyword": "五年级数学进阶",
        "directory_screenshot": make_file(tmp_path / "directory.png"),
        "content_screenshots": tuple(
            make_file(tmp_path / f"content-{index}.png")
            for index in range(1, content_count + 1)
        ),
        "cover_image": make_file(tmp_path / "cover.jpg"),
    }
    values.update(changes)
    return WechatArticleInput(**values)


def test_build_article_uses_locked_benchmark_template(tmp_path) -> None:
    source = article(tmp_path)

    artifact = build_wechat_article(source, output_dir=tmp_path / "output")
    rendered = artifact.html_path.read_text(encoding="utf-8")

    assert artifact.title == source.title
    assert artifact.screenshot_count == 3
    assert "五年级数学期末专项复习资料&lt;精选&gt;可打印练习试卷及答案解析完整合集" in rendered
    assert "覆盖一至六年级 &amp; 配套答案" in rendered
    assert rendered.count(source.new_link) == 3
    assert rendered.count("data-wechat-summary=") == 1
    assert rendered.count("data-content-source-url=") == 1
    parser = MetadataParser()
    parser.feed(rendered)
    assert parser.metadata == [
        {
            "style": "font-size:16px;line-height:1.85;color:#333;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue','PingFang SC','Microsoft YaHei',Arial,sans-serif;",
            "data-wechat-summary": source.description,
            "data-content-source-url": source.new_link,
        }
    ]
    required = [
        "今天跟大家分享的是：",
        "【获取完整资料】",
        "网盘转存之后下载才是无损文件",
        "【部分内容展示】👇👇👇",
        "【目录清单】",
        'data-shot-type="directory"',
        "【部分资料截图】",
        'data-shot-type="content"',
        "更多归档教辅",
        "background:#3869d8",
        "回复下面关键词免费下载",
        "【获取方法①】",
        "在公众号“发消息”回复：",
        source.keyword,
        "【获取方法②】",
        "【阅读原文】",
    ]
    positions = [rendered.index(fragment) for fragment in required]
    assert positions == sorted(positions)
    assert rendered.count('data-shot-type="directory"') == 1
    assert rendered.count('data-shot-type="content"') == 2
    assert 'color:#40bcbf' in rendered
    assert 'font-size:19px;color:#fa5359;font-weight:700' in rendered
    paths = (source.directory_screenshot, *source.content_screenshots)
    for path in paths:
        encoded = base64.b64encode(
            _compressed_jpeg(path, size=(1200, 1200), quality=76)
        ).decode("ascii")
        assert rendered.count(encoded) == 1
    assert rendered.index('data-source="directory.png"') < rendered.index('data-source="content-1.png"')
    assert rendered.index('data-source="content-1.png"') < rendered.index('data-source="content-2.png"')
    assert str(source.cover_image.resolve()) not in rendered
    assert source.cover_image.name not in rendered
    assert "dell" not in rendered.lower()
    assert base64.b64encode(source.cover_image.read_bytes()).decode("ascii") not in rendered
    assert "严禁任何商业转载" in rendered


def test_build_audio_article_uses_exactly_two_directory_images(tmp_path) -> None:
    source = article(tmp_path, content_count=1, directory_only=True)
    artifact = build_wechat_article(source, output_dir=tmp_path / "audio-output")
    rendered = artifact.html_path.read_text(encoding="utf-8")

    assert artifact.screenshot_count == 2
    assert rendered.count('data-shot-type="directory"') == 2
    assert 'data-shot-type="content"' not in rendered
    assert "【资料目录展示】" in rendered


@pytest.mark.parametrize(
    "title,code,message",
    [
        ("五年级数学资料", "TITLE_LENGTH", "30 to 60"),
        (LONG_WECHAT_TITLE, "TITLE_LENGTH", "30 to 60"),
        (VALID_WECHAT_TITLE + "\u200b", "TITLE_INVALID", None),
        (RISK_WECHAT_TITLE, "TITLE_POLICY_FAILED", None),
        (MISPLACED_PREFIX_TITLE, "TITLE_PREFIX", None),
    ],
)
def test_build_article_rejects_title_policy_before_writing_output(
    tmp_path, title, code, message, monkeypatch
) -> None:
    output = tmp_path / "out"
    if title == LONG_WECHAT_TITLE:
        assert len(title) == 61
    monkeypatch.setattr(
        "windows_capabilities.wechat_article._validate_image_file",
        lambda *args: pytest.fail("media validation must not run before title validation"),
    )
    source = WechatArticleInput(
        title=title,
        description="覆盖一至六年级 & 配套答案",
        new_link="https://pan.quark.cn/s/Abc123x",
        keyword="五年级数学进阶",
        directory_screenshot=tmp_path / "missing-directory.png",
        content_screenshots=(
            tmp_path / "missing-content-1.png",
            tmp_path / "missing-content-2.png",
        ),
        cover_image=tmp_path / "missing-cover.jpg",
    )

    with pytest.raises(TitlePolicyError) as error:
        build_wechat_article(source, output_dir=output)

    assert error.value.code == code
    if message:
        assert message in str(error.value)
    assert not output.exists()


def test_build_article_accepts_strategy_selected_title(tmp_path) -> None:
    selected = select_best_title(
        extract_title_facts(
            (
                "五年级数学期末资料 28专题 可打印练习试卷",
                "含答案解析 100讲知识清单",
            )
        )
    ).title

    artifact = build_wechat_article(article(tmp_path, title=selected), output_dir=tmp_path / "out")

    assert artifact.title == selected


def test_build_article_normalizes_title_whitespace_in_artifact_and_html(tmp_path) -> None:
    title = VALID_WECHAT_TITLE.replace("专项", "专项  \t")
    expected = VALID_WECHAT_TITLE.replace("专项", "专项 ")

    artifact = build_wechat_article(article(tmp_path, title=title), output_dir=tmp_path / "out")
    rendered = artifact.html_path.read_text(encoding="utf-8")

    assert artifact.title == expected
    assert escape(expected) in rendered
    assert "\t" not in artifact.title
    assert "  " not in artifact.title


def test_build_article_replaces_disallowed_publication_wording_everywhere(tmp_path) -> None:
    source = article(
        tmp_path,
        title="五年级数学视频课程训练营资料，计算图形重点题型专项练习与课后巩固，适合日常复习",
        description="这是一套数学课程训练营资料。",
        keyword="数学课程",
    )

    artifact = build_wechat_article(source, output_dir=tmp_path / "out")
    rendered = artifact.html_path.read_text(encoding="utf-8")

    assert "课程" not in rendered
    assert "训练营" not in rendered
    assert "资料资料" not in rendered


def test_build_article_sanitizes_policy_terms_from_visible_input_copy(tmp_path) -> None:
    source = article(
        tmp_path,
        description="人教版数学课程，更多资源",
        keyword="黄冈训练营",
    )

    rendered = build_wechat_article(source, output_dir=tmp_path / "out").html_path.read_text(
        encoding="utf-8"
    )

    assert "人教版" not in rendered
    assert "黄冈" not in rendered
    assert "更多资源" not in rendered
    assert "课程" not in rendered
    assert "训练营" not in rendered


@pytest.mark.parametrize("length", [30, 60])
def test_build_article_allows_title_length_bounds(tmp_path, length) -> None:
    title = "五年级数学" + "甲" * (length - len("五年级数学"))

    artifact = build_wechat_article(article(tmp_path, title=title), output_dir=tmp_path / "out")

    assert artifact.title == title


def test_build_article_compresses_png_screenshots_to_jpeg_for_wechat_api(tmp_path) -> None:
    source = article(tmp_path)
    rendered = build_wechat_article(source, output_dir=tmp_path / "output").html_path.read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in rendered
    assert "data:image/png;base64," not in rendered


def test_build_article_is_deterministic_and_uses_fixed_safe_filename(tmp_path) -> None:
    source = article(tmp_path)
    output = tmp_path / "nested" / "output"

    first = build_wechat_article(source, output_dir=output)
    first_bytes = first.html_path.read_bytes()
    second = build_wechat_article(source, output_dir=output)

    assert second.html_path == output.resolve() / "article-publish-embedded.html"
    assert second.html_path.read_bytes() == first_bytes


@pytest.mark.parametrize("count", [0, 1])
def test_build_article_requires_at_least_two_content_screenshots(tmp_path, count) -> None:
    with pytest.raises(ValueError, match="content_screenshots"):
        build_wechat_article(article(tmp_path, content_count=count), output_dir=tmp_path / "out")


def test_build_article_accepts_more_than_two_content_screenshots(tmp_path) -> None:
    artifact = build_wechat_article(article(tmp_path, content_count=3), output_dir=tmp_path / "out")
    assert artifact.screenshot_count == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("title", "bad\nline"),
        ("title", LONG_WECHAT_TITLE),
        ("description", "bad\rline"),
        ("keyword", ""),
        ("keyword", "bad\nline"),
        ("new_link", "http://pan.quark.cn/s/id"),
        ("new_link", "https://evil.test/s/id"),
        ("new_link", "https://pan.quark.cn/s/id?pwd=1"),
        ("new_link", "https://pan.quark.cn/s/id_with_underscore"),
        ("new_link", "https://pan.quark.cn/s/id-with-hyphen"),
    ],
)
def test_build_article_rejects_invalid_text_and_link(tmp_path, field, value) -> None:
    with pytest.raises(ValueError):
        build_wechat_article(article(tmp_path, **{field: value}), output_dir=tmp_path / "out")


@pytest.mark.parametrize("target", ["directory", "content", "cover"])
def test_build_article_rejects_missing_or_empty_media(tmp_path, target) -> None:
    source = article(tmp_path)
    path = {
        "directory": source.directory_screenshot,
        "content": source.content_screenshots[0],
        "cover": source.cover_image,
    }[target]
    path.write_bytes(b"")

    with pytest.raises(ValueError, match="file"):
        build_wechat_article(source, output_dir=tmp_path / "out")


def test_article_types_are_frozen(tmp_path) -> None:
    source = article(tmp_path)
    with pytest.raises(FrozenInstanceError):
        source.title = "changed"


@pytest.mark.parametrize("target", ["directory", "content", "cover"])
def test_build_article_rejects_corrupt_images(tmp_path, target) -> None:
    source = article(tmp_path)
    path = {
        "directory": source.directory_screenshot,
        "content": source.content_screenshots[0],
        "cover": source.cover_image,
    }[target]
    path.write_bytes(b"not-an-image")
    with pytest.raises(ValueError, match="image"):
        build_wechat_article(source, output_dir=tmp_path / "out")


def test_build_article_uses_actual_image_format_for_mime(tmp_path) -> None:
    disguised = make_file(tmp_path / "looks-like-png.png", "JPEG")
    source = article(tmp_path, directory_screenshot=disguised)
    rendered = build_wechat_article(source, output_dir=tmp_path / "out").html_path.read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in rendered
    assert "data:image/png;base64," not in rendered


def test_build_article_rejects_unsupported_image_format(tmp_path) -> None:
    gif = make_file(tmp_path / "image.gif", "GIF")
    source = article(tmp_path, directory_screenshot=gif)
    with pytest.raises(ValueError, match="PNG or JPEG"):
        build_wechat_article(source, output_dir=tmp_path / "out")


def test_build_article_rejects_oversized_source_before_read_bytes(tmp_path, monkeypatch) -> None:
    source = article(tmp_path)
    with source.directory_screenshot.open("ab") as stream:
        stream.truncate(MAX_SOURCE_IMAGE_BYTES + 1)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read_bytes must not be called"))
    with pytest.raises(ValueError, match="too large"):
        build_wechat_article(source, output_dir=tmp_path / "out")


def test_build_article_accepts_source_at_exact_size_limit(tmp_path) -> None:
    source = article(tmp_path)
    with source.directory_screenshot.open("ab") as stream:
        stream.truncate(MAX_SOURCE_IMAGE_BYTES)

    artifact = build_wechat_article(source, output_dir=tmp_path / "out")

    assert artifact.screenshot_count == 3
