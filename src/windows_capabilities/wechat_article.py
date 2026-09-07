from __future__ import annotations

import base64
from dataclasses import dataclass
import html
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile

from PIL import Image, UnidentifiedImageError

from .wechat_resource_pipeline.policy import (
    sanitize_publication_copy,
    validate_publication_html,
)
from .wechat_title_strategy import validate_publishable_title


DISCLAIMER_1 = (
    "本公众号所分享的各类资源均整理自网络，仅做免费公益分享，仅限个人学习、校内教学参考使用，"
    "严禁任何商业转载、售卖及商用盈利行为。所有内容著作权归原作者合法所有，若不慎涉及版权侵权，"
    "请您直接在公众号后台留言告知，我会第一时间核实处理。本公众号始终尊重并维护原创版权。"
)
DISCLAIMER_2 = (
    "平台所有分享资料均可通过网盘免费下载，不收取任何资料费用。下载速度取决于网盘自身服务器，"
    "开通网盘会员可提升下载速度；网盘会员充值属于网盘平台收费行为，与本公众号无任何关联，"
    "本公众号不会以任何形式收取资源服务费。"
)
_QUARK_LINK = re.compile(r"https://pan\.quark\.cn/s/[A-Za-z0-9]+\Z")
MAX_SOURCE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_PAYLOAD_BYTES = 20 * 1024 * 1024
LOCKED_TEMPLATE_FRAGMENTS = (
    "今天跟大家分享的是：",
    "【获取完整资料】",
    "网盘转存之后下载才是无损文件",
    "【部分内容展示】👇👇👇",
    "【目录清单】",
    "【部分资料截图】",
    "更多归档教辅",
    "background:#3869d8",
    "回复下面关键词免费下载",
    "【获取方法①】",
    "【获取方法②】",
)


@dataclass(frozen=True)
class WechatArticleInput:
    title: str
    description: str
    new_link: str
    keyword: str
    directory_screenshot: Path
    content_screenshots: tuple[Path, ...]
    cover_image: Path
    directory_only: bool = False


@dataclass(frozen=True)
class WechatArticleArtifact:
    title: str
    html_path: Path
    screenshot_count: int


def build_wechat_article(
    article: WechatArticleInput, *, output_dir: Path
) -> WechatArticleArtifact:
    if not isinstance(article, WechatArticleInput):
        raise TypeError("article must be WechatArticleInput")
    title = validate_publishable_title(article.title)
    description = sanitize_publication_copy(
        _single_line(article.description, "description", 500)
    )
    link = _single_line(article.new_link, "new_link", 200)
    if not is_canonical_quark_share_url(link):
        raise ValueError("new_link must be a Quark share URL")
    keyword = sanitize_publication_copy(_single_line(article.keyword, "keyword", 30))
    validate_publication_html("\n".join((title, description, keyword)))
    directory_screenshot = _validate_image_file(article.directory_screenshot, "directory screenshot")
    minimum = 1 if article.directory_only else 2
    if not isinstance(article.content_screenshots, tuple) or len(article.content_screenshots) < minimum:
        raise ValueError(f"content_screenshots must contain at least {minimum} files")
    content_screenshots = tuple(
        _validate_image_file(path, "content screenshot") for path in article.content_screenshots
    )
    _validate_image_file(article.cover_image, "cover image")
    if not isinstance(output_dir, Path):
        raise TypeError("output_dir must be Path")

    escaped_title = html.escape(title)
    escaped_description = html.escape(description)
    escaped_link = html.escape(link, quote=True)
    escaped_keyword = html.escape(keyword)
    directory_html = _embedded_screenshot(directory_screenshot[0], index=1, shot_type="directory")
    content_html = "".join(
        _embedded_screenshot(path, index=index, shot_type="directory" if article.directory_only else "content")
        for index, (path, _mime) in enumerate(content_screenshots, 1)
    )
    secondary_heading = "【资料目录展示】" if article.directory_only else "【部分资料截图】"
    rendered = (
        f'<section data-wechat-summary="{html.escape(description, quote=True)}" '
        f'data-content-source-url="{html.escape(link, quote=True)}" '
        'style="font-size:16px;line-height:1.85;color:#333;font-family:-apple-system,'
        "BlinkMacSystemFont,'Helvetica Neue','PingFang SC','Microsoft YaHei',Arial,sans-serif;\">\n"
        '<section style="margin:8px 0 28px;padding:1px;background:#def2ef;">'
        '<section style="margin:-8px 8px;padding:20px 15px 15px 20px;border:1px solid #75c8c0;background:#fff;letter-spacing:2px;">'
        '<p style="margin:0 0 10px;">今天跟大家分享的是：</p>'
        f'<p style="margin:0 0 12px;font-size:20px;font-weight:700;color:#d94743;">{escaped_title}</p>'
        f'<p style="margin:0;">{escaped_description}</p>'
        '<p style="margin:10px 0 0;">获取完整<span style="color:#0080ff;font-weight:700;">免费版</span>，请下拉至文末。</p>'
        '</section></section>'
        '<section style="margin:24px 0 22px;padding:18px 16px;border-top:2px solid #40bcbf;border-bottom:2px solid #40bcbf;text-align:center;">'
        '<p style="margin:0 0 8px;font-size:20px;font-weight:700;color:#8b170e;">【获取完整资料】</p>'
        '<p style="margin:0 0 6px;font-weight:700;">夸克网盘：</p>'
        f'<p style="margin:0 0 12px;word-break:break-all;"><a href="{escaped_link}" style="color:#40bcbf;text-decoration:none;font-weight:700;">{escaped_link}</a></p>'
        '<p style="margin:0;font-size:19px;color:#fa5359;font-weight:700;">网盘转存之后下载才是无损文件</p>'
        '</section>'
        '<p style="margin:28px 0 16px;font-size:21px;font-weight:700;text-align:center;">【部分内容展示】👇👇👇</p>'
        '<p style="margin:18px 0 12px;font-size:18px;font-weight:700;color:#8b170e;">【目录清单】</p>'
        f'{directory_html}'
        f'<p style="margin:24px 0 12px;font-size:18px;font-weight:700;color:#8b170e;">{secondary_heading}</p>'
        f'{content_html}'
        '<p style="margin:24px 0 14px;text-align:center;font-size:18px;font-weight:700;color:#8b170e;">&gt;&gt; 更多归档教辅，请点击下方 <span style="color:#004cff;">【阅读&#21407;文】</span></p>'
        '<section style="margin:10px 0 24px;padding:8px 10px;background:#3869d8;color:#fff;font-size:14px;line-height:1.9;text-align:justify;">'
        '<strong>声明：</strong>本公众号分享的电子资料版权归原作者及相关权利方所有，仅供学生、家长和教师下载预览、学习交流与教学参考，严禁任何商业转载、售卖及其他盈利行为。资料内容仅用于辅助判断是否适合当前学习阶段；如有侵权，请联系删除。网盘会员与下载服务由平台提供，与本公众号无关。'
        '</section>'
        '<section style="margin:20px 8px 28px;padding:22px 18px;background:#fff;border-radius:14px;box-shadow:0 3px 14px rgba(0,0,0,.12);text-align:center;">'
        '<p style="margin:0 0 8px;color:#666;">篇幅有限，仅展示部分内容</p>'
        '<p style="margin:0;color:#e6254b;font-size:19px;font-weight:700;">回复下面关键词免费下载</p>'
        '</section>'
        '<p style="margin:22px 0 12px;font-size:19px;font-weight:700;color:#00b800;">【获取方法①】</p>'
        '<p style="margin:0 0 8px;text-align:center;color:#0080ff;font-size:18px;font-weight:700;">在公众号“发消息”回复：</p>'
        f'<p style="margin:0 0 22px;text-align:center;color:#ff4b00;font-size:21px;font-weight:700;">{escaped_keyword}</p>'
        '<p style="margin:20px 0 10px;font-size:19px;font-weight:700;color:#8b170e;">【获取方法②】</p>'
        '<p style="margin:0 0 24px;text-align:center;color:#8b170e;font-size:18px;font-weight:700;">点击下方 <span style="color:#004cff;">【阅读原文】</span></p>'
        '<p style="margin:28px 0 8px;text-align:center;color:#7f5dbb;font-weight:700;">点赞 · 在看 · 分享，让更多需要的人看到</p>'
        '</section>\n'
    )
    if len(rendered.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("generated HTML is too large")
    validate_locked_wechat_template(rendered, directory_only=article.directory_only)

    destination_dir = output_dir.resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / "article-publish-embedded.html"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".wechat-article-", suffix=".tmp", dir=destination_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return WechatArticleArtifact(
        title=title,
        html_path=destination,
        screenshot_count=1 + len(content_screenshots),
    )


def is_canonical_quark_share_url(value: object) -> bool:
    return isinstance(value, str) and _QUARK_LINK.fullmatch(value) is not None


def validate_locked_wechat_template(content: str, *, directory_only: bool = False) -> None:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("article template is invalid")
    if any(term in content for term in ("课程", "训练营")):
        raise ValueError("article template contains disallowed publication wording")
    positions: list[int] = []
    fragments = tuple("【资料目录展示】" if directory_only and fragment == "【部分资料截图】" else fragment for fragment in LOCKED_TEMPLATE_FRAGMENTS)
    for fragment in fragments:
        position = content.find(fragment)
        if position < 0:
            raise ValueError("article template is incomplete")
        positions.append(position)
    if positions != sorted(positions):
        raise ValueError("article template order is invalid")
    if directory_only:
        if content.count('data-shot-type="directory"') != 2 or 'data-shot-type="content"' in content:
            raise ValueError("directory-only article requires exactly two directory screenshots")
    else:
        if content.count('data-shot-type="directory"') != 1:
            raise ValueError("article template requires one directory screenshot")
        if content.count('data-shot-type="content"') < 2:
            raise ValueError("article template requires two content screenshots")
    if (
        "color:#40bcbf" not in content
        or "font-size:19px;color:#fa5359;font-weight:700" not in content
    ):
        raise ValueError("article template colors are invalid")


def _single_line(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized or "\n" in normalized or "\r" in normalized or len(normalized) > maximum:
        raise ValueError(f"{field_name} must be a non-empty single line within {maximum} characters")
    return normalized


def _validate_image_file(path: object, label: str) -> tuple[Path, str]:
    if not isinstance(path, Path):
        raise ValueError(f"{label} must be a Path")
    resolved = path.resolve()
    try:
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            raise ValueError(f"{label} must be a non-empty regular file")
        if resolved.stat().st_size > MAX_SOURCE_IMAGE_BYTES:
            raise ValueError(f"{label} is too large")
    except OSError as exc:
        raise ValueError(f"{label} must be a readable regular file") from exc
    try:
        with Image.open(resolved) as image:
            image_format = image.format
            image.verify()
    except (OSError, SyntaxError, UnidentifiedImageError) as exc:
        raise ValueError(f"{label} must be a decodable image") from exc
    if image_format not in {"PNG", "JPEG"}:
        raise ValueError(f"{label} must use PNG or JPEG format")
    return resolved, "image/png" if image_format == "PNG" else "image/jpeg"


def _embedded_screenshot(path: Path, *, index: int, shot_type: str) -> str:
    # Match the proven publisher workflow: shrink browser PNGs to compact JPG
    # before embedding them in the HTML sent to wx.limyai.com.
    encoded = base64.b64encode(_compressed_jpeg(path, size=(1200, 1200), quality=76)).decode("ascii")
    alt = "夸克网盘内层目录截图" if shot_type == "directory" else "夸克网盘资料内容截图"
    return (
        f'<p style="margin:0 0 {"24" if index >= 2 else "14"}px;text-align:center;">'
        f'<img data-shot-type="{shot_type}" data-source="{html.escape(path.name, quote=True)}" '
        f'src="data:image/jpeg;base64,{encoded}" alt="{alt}" '
        'style="display:block;width:100%;max-width:634px;height:auto;margin:0 auto;"></p>'
    )


def _compressed_jpeg(path: Path, *, size: tuple[int, int] = (760, 550), quality: int = 48) -> bytes:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
        return output.getvalue()
