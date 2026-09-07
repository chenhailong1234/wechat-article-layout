from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from html.parser import HTMLParser
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import re
import threading
from types import ModuleType
from typing import Mapping, Protocol
from urllib.error import URLError

from pydantic import SecretStr

from .wechat_matrix import WechatAccount
from .wechat_title_strategy import TitlePolicyError, validate_publishable_title
from .wechat_article import (
    MAX_HTML_BYTES,
    MAX_PAYLOAD_BYTES,
    MAX_SOURCE_IMAGE_BYTES,
    _compressed_jpeg,
    _validate_image_file,
    is_canonical_quark_share_url,
    validate_locked_wechat_template,
)


DEFAULT_PUBLISHER_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "wechat_api.py"
)
_PUBLISHER_LOCK = threading.RLock()


class DraftPublishFailed(RuntimeError):
    """A safe, typed failure while validating or creating a WeChat draft."""

    def __init__(self, message: str, *, code: str = "REJECTED", retryable: bool = False) -> None:
        super().__init__(message)
        self._code = code
        self._retryable = retryable

    @property
    def code(self) -> str:
        return self._code

    @property
    def retryable(self) -> bool:
        return self._retryable


@dataclass(frozen=True)
class DraftRequest:
    title: str
    html_path: Path
    account: WechatAccount


@dataclass(frozen=True)
class DraftResult:
    account_id: str
    appid: str
    media_id: str
    status: str


class WechatPublisher(Protocol):
    def publish_html(
        self, *, appid: str, api_key: str, title: str, html: str, cover_image: Path
    ) -> dict[object, object]: ...


def create_draft(request: DraftRequest, publisher: WechatPublisher) -> DraftResult:
    if not isinstance(request, DraftRequest):
        raise DraftPublishFailed("invalid draft request")
    try:
        title = validate_publishable_title(request.title)
    except TitlePolicyError:
        raise DraftPublishFailed(
            "invalid title", code="TITLE_POLICY_FAILED", retryable=False
        ) from None
    account = request.account
    if not isinstance(account, WechatAccount):
        raise DraftPublishFailed("invalid account")
    account_id = _required_line(account.id, "account id", 128)
    appid = _required_line(account.appid, "appid", 128)
    if not isinstance(account.api_key, SecretStr):
        raise DraftPublishFailed("invalid account credential")
    try:
        cover, _ = _validate_image_file(account.cover_image, "cover image")
    except ValueError:
        raise DraftPublishFailed("invalid cover image") from None
    html_path = _nonempty_file(
        request.html_path,
        "HTML file",
        maximum=MAX_HTML_BYTES,
        too_large_code="HTML_TOO_LARGE",
    )
    try:
        content = html_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DraftPublishFailed("unable to read UTF-8 HTML") from exc
    if not content.strip():
        raise DraftPublishFailed("HTML file is empty")
    try:
        validate_locked_wechat_template(content)
    except ValueError:
        raise DraftPublishFailed("article template validation failed") from None

    try:
        response = publisher.publish_html(
            appid=appid,
            api_key=account.api_key.get_secret_value(),
            title=title,
            html=content,
            cover_image=cover,
        )
    except DraftPublishFailed:
        raise
    except Exception:
        raise DraftPublishFailed("draft publisher failed") from None
    if not isinstance(response, dict):
        raise DraftPublishFailed("draft creation was rejected")
    if response.get("success") is False:
        code, retryable = _classify_failure_signals(
            tuple(_response_classification_signals(response))
        )
        raise _classified_failure(code, retryable)
    data = response.get("data", response)
    if not isinstance(data, dict):
        raise DraftPublishFailed("draft response is invalid")
    media_id = data.get("mediaId")
    status = data.get("status")
    if not isinstance(media_id, str) or not media_id.strip():
        raise DraftPublishFailed("draft response missing media id")
    if not isinstance(status, str) or not status.strip():
        raise DraftPublishFailed("draft response missing status")
    for container in (response, data):
        for key in ("wechatAppid", "appid"):
            if key in container and container[key] != appid:
                raise DraftPublishFailed("draft response account mismatch")
    return DraftResult(account_id, appid, media_id.strip(), status.strip())


class InstalledWechatPublisher:
    def __init__(
        self,
        *,
        script_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        module: ModuleType | object | None = None,
    ) -> None:
        environment = environ if environ is not None else os.environ
        configured = environment.get("WECHAT_PUBLISHER_SCRIPT")
        self._script_path = script_path or (Path(configured) if configured else DEFAULT_PUBLISHER_SCRIPT)
        self._module = module

    def publish_html(
        self, *, appid: str, api_key: str, title: str, html: str, cover_image: Path
    ) -> dict[object, object]:
        if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
            raise DraftPublishFailed(
                "article HTML is too large", code="HTML_TOO_LARGE", retryable=False
            )
        summary, source_url = _extract_article_metadata(html)
        try:
            cover, _ = _validate_image_file(cover_image, "cover image")
        except ValueError as exc:
            message = "cover image is too large" if "too large" in str(exc) else "invalid cover image"
            raise DraftPublishFailed(message, code="IMAGE_INVALID", retryable=False) from None
        payload = {
            "wechatAppid": appid,
            "title": title,
            "content": html,
            "summary": summary[:110],
            "coverImage": "data:image/jpeg;base64," + base64.b64encode(
                _compressed_jpeg(cover, size=(760, 760), quality=50)
            ).decode("ascii"),
            "contentFormat": "html",
            "articleType": "news",
            "contentSourceUrl": source_url,
        }
        payload_size = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if payload_size > MAX_PAYLOAD_BYTES:
            raise DraftPublishFailed(
                "draft payload is too large", code="PAYLOAD_TOO_LARGE", retryable=False
            )

        with _PUBLISHER_LOCK:
            module = self._module or self._load_module()
            request = getattr(module, "make_api_request", None)
            original_get_key = getattr(module, "get_api_key", None)
            if not callable(request) or not callable(original_get_key):
                raise DraftPublishFailed("installed publisher API is incompatible")
            output = StringIO()
            errors = StringIO()
            setattr(module, "get_api_key", lambda: api_key)
            try:
                with redirect_stdout(output), redirect_stderr(errors):
                    response = request("/wechat-publish", payload)
            except (SystemExit, Exception) as exc:
                raise _classify_publisher_failure(exc, errors.getvalue()) from None
            finally:
                setattr(module, "get_api_key", original_get_key)
        if not isinstance(response, dict):
            raise DraftPublishFailed("installed publisher returned an invalid response")
        return response

    def _load_module(self) -> ModuleType:
        path = self._script_path.resolve()
        if not path.is_file():
            raise DraftPublishFailed("installed publisher script was not found")
        try:
            spec = importlib.util.spec_from_file_location("windows_capabilities_installed_wechat_api", path)
            if spec is None or spec.loader is None:
                raise ImportError
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            raise DraftPublishFailed("installed publisher script could not be loaded") from None
        self._module = module
        return module


def _required_line(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise DraftPublishFailed(f"invalid {label}")
    normalized = value.strip()
    if not normalized or "\r" in normalized or "\n" in normalized or len(normalized) > maximum:
        raise DraftPublishFailed(f"invalid {label}")
    return normalized


def _nonempty_file(
    value: object,
    label: str,
    *,
    maximum: int | None = None,
    too_large_code: str = "REJECTED",
) -> Path:
    if not isinstance(value, Path):
        raise DraftPublishFailed(f"invalid {label}")
    path = value.resolve()
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            raise DraftPublishFailed(f"invalid {label}")
        if maximum is not None and path.stat().st_size > maximum:
            raise DraftPublishFailed(
                f"{label} is too large", code=too_large_code, retryable=False
            )
    except OSError as exc:
        raise DraftPublishFailed(f"invalid {label}") from exc
    return path


def _classify_publisher_failure(
    error: BaseException, captured_stderr: str
) -> DraftPublishFailed:
    signals = (
            type(error).__name__,
            error,
            captured_stderr,
            getattr(error, "code", ""),
            getattr(error, "status", ""),
            getattr(error, "status_code", ""),
    )
    code, retryable = _classify_failure_signals(
        signals,
        network_error=isinstance(
            error, (URLError, TimeoutError, ConnectionError, OSError)
        ),
    )
    return _classified_failure(code, retryable)


def _classify_failure_signals(
    signals: tuple[object, ...], *, network_error: bool = False
) -> tuple[str, bool]:
    """Classify safe signals without retaining or returning their original text."""
    details = " ".join(str(value) for value in signals).lower()
    if any(
        marker in details
        for marker in (
            "api_key_invalid",
            "account_token_expired",
            "auth_failed",
            "auth_expired",
            "401",
            "403",
            "authentication",
            "unauthorized",
            "forbidden",
        )
    ):
        return "AUTH_FAILED", False
    if any(
        marker in details
        for marker in (
            "429",
            "频率",
            "操作频繁",
            "too frequent",
            "rate limit",
            "rate_limit",
        )
    ):
        return "RATE_LIMIT", True
    if "server_busy" in details or re.search(r"\b5\d\d\b", details):
        return "SERVER_ERROR", True
    if network_error or any(
        marker in details
        for marker in ("network", "timeout", "timed out", "connection")
    ):
        return "NETWORK", True
    return "REJECTED", False


def _classified_failure(code: str, retryable: bool) -> DraftPublishFailed:
    messages = {
        "AUTH_FAILED": "publisher authentication failed",
        "RATE_LIMIT": "publisher rate limit reached",
        "NETWORK": "publisher network error",
        "SERVER_ERROR": "publisher server error",
        "REJECTED": "publisher rejected draft",
    }
    safe_code = code if code in messages else "REJECTED"
    return DraftPublishFailed(
        messages[safe_code], code=safe_code, retryable=retryable if safe_code != "REJECTED" else False
    )


def _response_classification_signals(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"code", "status", "statusCode", "httpStatus", "errorCode"}:
                yield item
            elif isinstance(item, (dict, list, tuple)):
                yield from _response_classification_signals(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _response_classification_signals(item)


class _ArticleMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.summaries: list[str | None] = []
        self.source_urls: list[str | None] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "data-wechat-summary":
                self.summaries.append(value)
            elif name == "data-content-source-url":
                self.source_urls.append(value)
            elif tag == "a" and name == "href" and isinstance(value, str):
                self.links.append(value)


def _extract_article_metadata(content: object) -> tuple[str, str]:
    if not isinstance(content, str):
        raise DraftPublishFailed("article HTML is invalid")
    parser = _ArticleMetadataParser()
    try:
        parser.feed(content)
        parser.close()
    except Exception:
        raise DraftPublishFailed("article metadata could not be parsed") from None
    if len(parser.summaries) != 1 or len(parser.source_urls) != 1:
        raise DraftPublishFailed("article metadata is missing or conflicting")
    summary = parser.summaries[0]
    source_url = parser.source_urls[0]
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or summary != summary.strip()
        or "\r" in summary
        or "\n" in summary
    ):
        raise DraftPublishFailed("article summary metadata is invalid")
    if not is_canonical_quark_share_url(source_url):
        raise DraftPublishFailed("article source URL metadata is invalid")
    quark_links = [link for link in parser.links if link.startswith("https://pan.quark.cn/")]
    if not quark_links or any(link != source_url for link in quark_links):
        raise DraftPublishFailed("article source URL mismatch")
    return summary, source_url
