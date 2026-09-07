import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from PIL import Image
from pydantic import SecretStr

from windows_capabilities.wechat_matrix import WechatAccount
from windows_capabilities.wechat_title_strategy import extract_title_facts, select_best_title
from windows_capabilities.wechat_pipeline import (
    DraftPublishFailed,
    DraftRequest,
    InstalledWechatPublisher,
    MAX_HTML_BYTES,
    MAX_SOURCE_IMAGE_BYTES,
    create_draft,
)


VALID_WECHAT_TITLE = "五年级数学期末专项复习资料，可打印练习试卷及答案解析完整合集"
LONG_WECHAT_TITLE = VALID_WECHAT_TITLE + "知识清单同步训练重点难点梳理课后巩固练习精选汇编真题精编课堂版"
RISK_WECHAT_TITLE = "五年级数学人教版期末专项复习资料可打印练习试卷答案解析完整合集"
MISPLACED_PREFIX_TITLE = "期末资料五年级数学可打印练习试卷及答案解析完整合集知识清单重点"


def write_image(path: Path, image_format: str = "PNG") -> Path:
    Image.new("RGB", (2, 2), "blue").save(path, format=image_format)
    return path


def make_account(tmp_path: Path, secret: str = "top-secret") -> WechatAccount:
    cover = tmp_path / "cover.png"
    write_image(cover)
    return WechatAccount("only", "wx-123", "KEY_ONE", SecretStr(secret), cover)


def make_request(tmp_path: Path, secret: str = "top-secret") -> DraftRequest:
    html = tmp_path / "article.html"
    html.write_text(self_describing_html(), encoding="utf-8")
    return DraftRequest(VALID_WECHAT_TITLE, html, make_account(tmp_path, secret))


class FakePublisher:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def publish_html(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def self_describing_html(
    summary: str = "摘要 & 更多", source_url: str = "https://pan.quark.cn/s/Abc123"
) -> str:
    escaped_summary = summary.replace("&", "&amp;").replace('"', "&quot;")
    return (
        f'<section data-wechat-summary="{escaped_summary}" '
        f'data-content-source-url="{source_url}">'
        '<p>今天跟大家分享的是：</p>'
        '<p>【获取完整资料】</p>'
        f'<a href="{source_url}" style="color:#40bcbf">{source_url}</a>'
        '<p style="font-size:19px;color:#fa5359;font-weight:700">网盘转存之后下载才是无损文件</p>'
        '<p>【部分内容展示】👇👇👇</p><p>【目录清单】</p>'
        '<img data-shot-type="directory" src="data:image/jpeg;base64,AA==">'
        '<p>【部分资料截图】</p>'
        '<img data-shot-type="content" src="data:image/jpeg;base64,AQ==">'
        '<img data-shot-type="content" src="data:image/jpeg;base64,Ag==">'
        '<p>更多归档教辅</p><section style="background:#3869d8">声明</section>'
        '<p>回复下面关键词免费下载</p><p>【获取方法①】</p><p>【获取方法②】</p>'
        '</section>'
    )


def test_create_draft_calls_only_selected_account_and_returns_evidence(tmp_path) -> None:
    request = make_request(tmp_path)
    publisher = FakePublisher({"success": True, "data": {"mediaId": "media-1", "status": "draft", "wechatAppid": "wx-123"}})

    result = create_draft(request, publisher)

    assert len(publisher.calls) == 1
    assert publisher.calls[0] == {
        "appid": "wx-123",
        "api_key": "top-secret",
        "title": VALID_WECHAT_TITLE,
        "html": self_describing_html(),
        "cover_image": request.account.cover_image,
    }
    assert result.account_id == "only"
    assert result.appid == "wx-123"
    assert result.media_id == "media-1"
    assert result.status == "draft"
    assert "top-secret" not in repr(result)


@pytest.mark.parametrize(
    "title",
    [
        "五年级数学资料",
        LONG_WECHAT_TITLE,
        VALID_WECHAT_TITLE + "\u200b",
        RISK_WECHAT_TITLE,
        MISPLACED_PREFIX_TITLE,
    ],
)
def test_create_draft_rejects_invalid_title_before_unwrapping_secret_or_publishing(
    tmp_path, title, monkeypatch
) -> None:
    class CountingSecret(SecretStr):
        calls = 0

        def get_secret_value(self):
            self.calls += 1
            return super().get_secret_value()

    secret = CountingSecret("top-secret")
    if title == LONG_WECHAT_TITLE:
        assert len(title) == 61
    account = WechatAccount(
        "only", "wx-123", "KEY_ONE", SecretStr("placeholder"), tmp_path / "missing-cover.png"
    )
    object.__setattr__(account, "api_key", secret)
    request = DraftRequest(title, tmp_path / "missing-article.html", account)
    publisher = FakePublisher({"success": True, "mediaId": "m", "status": "draft"})
    monkeypatch.setattr(
        "windows_capabilities.wechat_pipeline._validate_image_file",
        lambda *args: pytest.fail("media validation must not run before title validation"),
    )
    monkeypatch.setattr(
        "windows_capabilities.wechat_pipeline._nonempty_file",
        lambda *args, **kwargs: pytest.fail("HTML validation must not run before title validation"),
    )

    with pytest.raises(DraftPublishFailed) as error:
        create_draft(request, publisher)

    assert error.value.code == "TITLE_POLICY_FAILED"
    assert error.value.retryable is False
    assert str(error.value) == "invalid title"
    assert publisher.calls == []
    assert secret.calls == 0


def test_create_draft_accepts_strategy_selected_title(tmp_path) -> None:
    selected = select_best_title(
        extract_title_facts(
            (
                "五年级数学期末资料 28专题 可打印练习试卷",
                "含答案解析 100讲知识清单",
            )
        )
    ).title
    request = make_request(tmp_path)
    request = DraftRequest(selected, request.html_path, request.account)
    publisher = FakePublisher({"success": True, "mediaId": "m", "status": "draft"})

    create_draft(request, publisher)

    assert publisher.calls[0]["title"] == selected


def test_create_draft_normalizes_title_whitespace_in_publisher_payload(tmp_path) -> None:
    title = VALID_WECHAT_TITLE.replace("专项", "专项  \t")
    expected = VALID_WECHAT_TITLE.replace("专项", "专项 ")
    request = make_request(tmp_path)
    request = DraftRequest(title, request.html_path, request.account)
    publisher = FakePublisher({"success": True, "mediaId": "m", "status": "draft"})

    create_draft(request, publisher)

    assert publisher.calls[0]["title"] == expected
    assert "\t" not in publisher.calls[0]["title"]
    assert "  " not in publisher.calls[0]["title"]


@pytest.mark.parametrize("length", [30, 60])
def test_create_draft_accepts_title_length_bounds(tmp_path, length) -> None:
    title = "五年级数学" + "甲" * (length - len("五年级数学"))
    request = make_request(tmp_path)
    request = DraftRequest(title, request.html_path, request.account)
    publisher = FakePublisher({"success": True, "mediaId": "m", "status": "draft"})

    result = create_draft(request, publisher)

    assert result.media_id == "m"
    assert publisher.calls[0]["title"] == title


def test_create_draft_unwraps_secret_only_at_publisher_boundary(tmp_path) -> None:
    class CountingSecret(SecretStr):
        calls = 0

        def get_secret_value(self):
            self.calls += 1
            return super().get_secret_value()

    secret = CountingSecret("secret")
    account = make_account(tmp_path)
    object.__setattr__(account, "api_key", secret)
    request = make_request(tmp_path)
    object.__setattr__(request, "account", account)
    publisher = FakePublisher({"success": True, "mediaId": "m", "status": "draft"})

    create_draft(request, publisher)

    assert secret.calls == 1


@pytest.mark.parametrize(
    "response",
    [
        {"success": False, "error": "top-secret"},
        {"success": True, "data": {"status": "draft"}},
        {"success": True, "data": {"mediaId": "m", "status": "draft", "wechatAppid": "wrong"}},
        {"success": True, "wechatAppid": "wrong", "data": {"mediaId": "m", "status": "draft", "wechatAppid": "wx-123"}},
        {"mediaId": "m"},
    ],
)
def test_create_draft_rejects_bad_responses_without_secret(tmp_path, response) -> None:
    with pytest.raises(DraftPublishFailed) as caught:
        create_draft(make_request(tmp_path), FakePublisher(response))
    assert "top-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "response,code,retryable",
    [
        ({"success": False, "code": "API_KEY_INVALID"}, "AUTH_FAILED", False),
        ({"success": False, "data": {"code": "ACCOUNT_TOKEN_EXPIRED"}}, "AUTH_FAILED", False),
        ({"success": False, "status": 401}, "AUTH_FAILED", False),
        ({"success": False, "data": {"status": "403"}}, "AUTH_FAILED", False),
        ({"success": False, "code": "RATE_LIMIT"}, "RATE_LIMIT", True),
        ({"success": False, "data": {"status": 429}}, "RATE_LIMIT", True),
        ({"success": False, "data": {"code": "操作频率过高"}}, "RATE_LIMIT", True),
        ({"success": False, "status": 503}, "SERVER_ERROR", True),
        ({"success": False, "data": {"code": "SERVER_BUSY"}}, "SERVER_ERROR", True),
        ({"success": False, "code": "NETWORK"}, "NETWORK", True),
        ({"success": False, "code": "INVALID_PARAMETER"}, "REJECTED", False),
    ],
)
def test_create_draft_classifies_unsuccessful_response_safely(
    tmp_path, response, code, retryable
) -> None:
    response["error"] = "top-secret error"
    response["message"] = "top-secret message"
    response["body"] = {"credential": "top-secret body"}

    with pytest.raises(DraftPublishFailed) as caught:
        create_draft(make_request(tmp_path), FakePublisher(response))

    assert caught.value.code == code
    assert caught.value.retryable is retryable
    rendered = str(caught.value) + repr(caught.value) + repr(caught.value.args)
    assert "top-secret" not in rendered


def test_create_draft_accepts_top_level_compatible_response(tmp_path) -> None:
    result = create_draft(
        make_request(tmp_path),
        FakePublisher({"success": True, "mediaId": "media-top", "status": "draft", "appid": "wx-123"}),
    )
    assert result.media_id == "media-top"


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "bad-utf8"])
def test_create_draft_validates_html_before_publisher_call(tmp_path, kind) -> None:
    request = make_request(tmp_path)
    if kind == "missing":
        request.html_path.unlink()
    elif kind == "directory":
        request.html_path.unlink()
        request.html_path.mkdir()
    elif kind == "empty":
        request.html_path.write_bytes(b"")
    else:
        request.html_path.write_bytes(b"\xff")
    publisher = FakePublisher({"success": True})

    with pytest.raises(DraftPublishFailed):
        create_draft(request, publisher)
    assert publisher.calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.replace("【目录清单】", ""),
        lambda value: value.replace(
            'data-shot-type="directory"', 'data-shot-type="content"'
        ),
        lambda value: value.replace(
            'data-shot-type="content"', 'data-shot-type="directory"'
        ),
        lambda value: value.replace("background:#3869d8", "background:#fff"),
        lambda value: value.replace("网盘转存之后下载才是无损文件", ""),
        lambda value: value.replace("【获取方法②】", ""),
    ],
)
def test_create_draft_rejects_noncanonical_template_before_publish(
    tmp_path, mutation
) -> None:
    request = make_request(tmp_path)
    request.html_path.write_text(
        mutation(request.html_path.read_text(encoding="utf-8")), encoding="utf-8"
    )
    publisher = FakePublisher({"success": True})

    with pytest.raises(DraftPublishFailed, match="template"):
        create_draft(request, publisher)

    assert publisher.calls == []


def test_installed_publisher_rejects_body_link_that_differs_from_source_url(
    tmp_path
) -> None:
    calls = []
    module = SimpleNamespace(
        get_api_key=lambda: "old",
        make_api_request=lambda endpoint, payload: calls.append(payload),
    )
    cover = write_image(tmp_path / "cover.png")
    html = self_describing_html().replace(
        'href="https://pan.quark.cn/s/Abc123"',
        'href="https://pan.quark.cn/s/Different456"',
    )

    with pytest.raises(DraftPublishFailed, match="source URL mismatch"):
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="key", title=VALID_WECHAT_TITLE,
            html=html, cover_image=cover,
        )

    assert calls == []


def test_installed_publisher_builds_payload_without_environment_mutation(tmp_path, monkeypatch) -> None:
    calls = []
    module = SimpleNamespace(
        get_api_key=lambda: "environment-key",
        make_api_request=lambda endpoint, payload: calls.append((endpoint, payload)) or {"success": True},
    )
    monkeypatch.setenv("WECHAT_API_KEY", "unchanged")
    adapter = InstalledWechatPublisher(module=module)
    cover = tmp_path / "cover.png"
    write_image(cover)

    response = adapter.publish_html(
        appid="wx-123", api_key="explicit-key", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
    )

    assert response == {"success": True}
    assert os.environ["WECHAT_API_KEY"] == "unchanged"
    assert module.get_api_key() == "environment-key"
    endpoint, payload = calls[0]
    assert endpoint == "/wechat-publish"
    assert payload["wechatAppid"] == "wx-123"
    assert payload["contentFormat"] == "html"
    assert payload["articleType"] == "news"
    assert payload["summary"] == "摘要 & 更多"
    assert payload["contentSourceUrl"] == "https://pan.quark.cn/s/Abc123"
    assert payload["coverImage"].startswith("data:image/jpeg;base64,")
    assert "explicit-key" not in repr(payload)


@pytest.mark.parametrize("error", [SystemExit(1), OSError("secret network detail")])
def test_installed_publisher_wraps_failures_without_secret(tmp_path, error) -> None:
    def fail(endpoint, payload):
        raise error

    module = SimpleNamespace(get_api_key=lambda: "old", make_api_request=fail)
    cover = tmp_path / "cover.png"
    write_image(cover)

    with pytest.raises(DraftPublishFailed) as caught:
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="secret", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
        )
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert module.get_api_key() == "old"


def test_installed_publisher_loads_configured_script_offline(tmp_path) -> None:
    script = tmp_path / "wechat_api.py"
    script.write_text(
        "def get_api_key(): return 'old'\n"
        "def make_api_request(endpoint, payload):\n"
        "    return {'success': True, 'data': {'mediaId': get_api_key(), 'status': endpoint}}\n",
        encoding="utf-8",
    )
    cover = tmp_path / "cover.jpg"
    write_image(cover, "JPEG")
    adapter = InstalledWechatPublisher(environ={"WECHAT_PUBLISHER_SCRIPT": str(script)})

    response = adapter.publish_html(
        appid="wx", api_key="explicit", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
    )

    assert response["data"] == {"mediaId": "explicit", "status": "/wechat-publish"}


def test_installed_publisher_truncates_summary_to_110_characters(tmp_path) -> None:
    calls = []
    module = SimpleNamespace(
        get_api_key=lambda: "old",
        make_api_request=lambda endpoint, payload: calls.append(payload) or {"success": True},
    )
    cover = tmp_path / "cover.jpg"
    write_image(cover, "JPEG")

    InstalledWechatPublisher(module=module).publish_html(
        appid="wx",
        api_key="key",
        title=VALID_WECHAT_TITLE,
        html=self_describing_html("中" * 111),
        cover_image=cover,
    )

    assert calls[0]["summary"] == "中" * 110


@pytest.mark.parametrize(
    "html",
    [
        "<p>no metadata</p>",
        '<section data-wechat-summary="summary" data-content-source-url="https://pan.quark.cn/s/id-with-hyphen"></section>',
        '<section data-wechat-summary="summary" data-content-source-url="https://pan.quark.cn/s/Abc"></section>'
        '<section data-wechat-summary="other" data-content-source-url="https://pan.quark.cn/s/Def"></section>',
    ],
)
def test_installed_publisher_rejects_missing_conflicting_or_invalid_metadata_before_request(tmp_path, html) -> None:
    calls = []
    module = SimpleNamespace(
        get_api_key=lambda: "old",
        make_api_request=lambda endpoint, payload: calls.append(payload),
    )
    cover = tmp_path / "cover.jpg"
    write_image(cover, "JPEG")

    with pytest.raises(DraftPublishFailed):
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=html, cover_image=cover
        )

    assert calls == []


def test_publisher_uses_actual_cover_format_for_mime(tmp_path) -> None:
    calls = []
    module = SimpleNamespace(
        get_api_key=lambda: "old",
        make_api_request=lambda endpoint, payload: calls.append(payload) or {"success": True},
    )
    cover = write_image(tmp_path / "disguised.jpg", "PNG")
    InstalledWechatPublisher(module=module).publish_html(
        appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
    )
    assert calls[0]["coverImage"].startswith("data:image/jpeg;base64,")


def test_publisher_rejects_oversized_cover_before_read_bytes(tmp_path, monkeypatch) -> None:
    cover = write_image(tmp_path / "cover.png")
    with cover.open("ab") as stream:
        stream.truncate(MAX_SOURCE_IMAGE_BYTES + 1)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read_bytes must not be called"))
    module = SimpleNamespace(get_api_key=lambda: "old", make_api_request=lambda *args: pytest.fail("no request"))
    with pytest.raises(DraftPublishFailed, match="large"):
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
        )


def test_publisher_rejects_oversized_html_before_cover_read(tmp_path, monkeypatch) -> None:
    cover = write_image(tmp_path / "cover.png")
    html = self_describing_html() + ("x" * MAX_HTML_BYTES)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("cover must not be read"))
    module = SimpleNamespace(get_api_key=lambda: "old", make_api_request=lambda *args: pytest.fail("no request"))
    with pytest.raises(DraftPublishFailed) as caught:
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=html, cover_image=cover
        )
    assert caught.value.code == "HTML_TOO_LARGE"


def test_publisher_rejects_total_payload_limit(tmp_path, monkeypatch) -> None:
    cover = write_image(tmp_path / "cover.png")
    monkeypatch.setattr("windows_capabilities.wechat_pipeline.MAX_PAYLOAD_BYTES", 200)
    module = SimpleNamespace(get_api_key=lambda: "old", make_api_request=lambda *args: pytest.fail("no request"))
    with pytest.raises(DraftPublishFailed) as caught:
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
        )
    assert caught.value.code == "PAYLOAD_TOO_LARGE"


def test_publisher_accepts_html_at_exact_size_limit(tmp_path) -> None:
    calls = []
    module = SimpleNamespace(
        get_api_key=lambda: "old",
        make_api_request=lambda endpoint, payload: calls.append(payload) or {"success": True},
    )
    cover = write_image(tmp_path / "cover.png")
    prefix = self_describing_html()
    html = prefix + ("x" * (MAX_HTML_BYTES - len(prefix.encode("utf-8"))))

    InstalledWechatPublisher(module=module).publish_html(
        appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=html, cover_image=cover
    )

    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["corrupt", "unsupported"])
def test_publisher_rejects_invalid_cover_format(tmp_path, kind) -> None:
    cover = tmp_path / "cover.img"
    if kind == "corrupt":
        cover.write_bytes(b"not an image")
    else:
        write_image(cover, "GIF")
    module = SimpleNamespace(get_api_key=lambda: "old", make_api_request=lambda *args: pytest.fail("no request"))
    with pytest.raises(DraftPublishFailed) as caught:
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="key", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
        )
    assert caught.value.code == "IMAGE_INVALID"


def test_global_publisher_lock_isolates_keys_and_output_across_instances(tmp_path, capsys) -> None:
    original = lambda: "original"
    records = []
    records_lock = threading.Lock()
    module = SimpleNamespace(get_api_key=original)

    def request(endpoint, payload):
        time.sleep(0.05)
        observed = module.get_api_key()
        print(f"stdout-{observed}")
        print(f"stderr-{observed}", file=sys.stderr)
        with records_lock:
            records.append((payload["title"], observed))
        return {"success": True}

    module.make_api_request = request
    cover = write_image(tmp_path / "cover.png")
    barrier = threading.Barrier(2)
    errors = []

    def worker(title, key):
        try:
            barrier.wait()
            InstalledWechatPublisher(module=module).publish_html(
                appid="wx", api_key=key, title=title, html=self_describing_html(), cover_image=cover
            )
        except BaseException as error:
            errors.append(error)

    titles = (VALID_WECHAT_TITLE + "一", VALID_WECHAT_TITLE + "二")
    threads = [
        threading.Thread(target=worker, args=(titles[0], "key-one")),
        threading.Thread(target=worker, args=(titles[1], "key-two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(records) == [(titles[0], "key-one"), (titles[1], "key-two")]
    assert module.get_api_key is original
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "stderr,error,code,retryable",
    [
        ("API_KEY_INVALID secret-value", SystemExit(1), "AUTH_FAILED", False),
        ("ACCOUNT_TOKEN_EXPIRED", SystemExit(1), "AUTH_FAILED", False),
        ("429 操作频率过高", SystemExit(1), "RATE_LIMIT", True),
        ("HTTP 503", SystemExit(1), "SERVER_ERROR", True),
        ("", URLError("network secret-value"), "NETWORK", True),
        ("request rejected", SystemExit(1), "REJECTED", False),
    ],
)
def test_publisher_classifies_failures_safely(tmp_path, stderr, error, code, retryable) -> None:
    def request(endpoint, payload):
        print(stderr, file=sys.stderr)
        raise error

    module = SimpleNamespace(get_api_key=lambda: "old", make_api_request=request)
    cover = write_image(tmp_path / "cover.png")
    with pytest.raises(DraftPublishFailed) as caught:
        InstalledWechatPublisher(module=module).publish_html(
            appid="wx", api_key="secret-value", title=VALID_WECHAT_TITLE, html=self_describing_html(), cover_image=cover
        )
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "secret-value" not in str(caught.value)
    assert "secret-value" not in repr(caught.value)
    with pytest.raises(AttributeError):
        caught.value.code = "CHANGED"
