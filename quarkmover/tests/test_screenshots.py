from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from quarkmover.screenshots import (
    Entry,
    DrissionPageSelectors,
    DrissionPageShareBrowser,
    NoUsableScreenshot,
    ResourceScreenshotBundle,
    PublicResourceRoute,
    ScreenshotFailed,
    capture_directory_screenshots,
    capture_resource_screenshots,
    _capture_with_playwright,
    _resolve_public_directory_fids,
    _resolve_public_resource_route,
    _entry_preview_urls,
    _select_content_screenshots,
    validate_resource_screenshot_bundle,
)


class FakeShareBrowser:
    def __init__(self, levels: dict[str, list[Entry]]) -> None:
        self.levels = levels
        self.parts: list[str] = []
        self.captured_levels: list[str] = []
        self.opened_url: str | None = None

    @property
    def level(self) -> str:
        return "/".join(self.parts) or "root"

    def open_share(self, share_url: str) -> None:
        self.opened_url = share_url
        self.parts = []

    def list_entries(self) -> list[Entry]:
        return self.levels.get(self.level, [])

    def enter_directory(self, entry: Entry) -> None:
        self.parts.append(entry.name)

    def leave_directory(self) -> None:
        self.parts.pop()

    def capture(self, path: Path) -> None:
        self.captured_levels.append(self.level)
        Image.new("RGB", (2, 2), "white").save(path, format="PNG")


def test_directory_playwright_capture_reads_output_as_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def run_helper(argv, **kwargs):
        calls.append(kwargs)
        for index in (1, 2):
            Image.new("RGB", (2, 2), "white").save(
                tmp_path / f"directory-{index}.png", format="PNG"
            )
        return subprocess.CompletedProcess(argv, 0, stdout=b"\xaa", stderr=b"")

    monkeypatch.setattr("quarkmover.screenshots.subprocess.run", run_helper)

    _capture_with_playwright("https://pan.quark.cn/s/new", tmp_path, ("fid",))

    assert calls[0]["text"] is False


def test_capture_skips_root_and_captures_two_inner_levels(tmp_path: Path) -> None:
    browser = FakeShareBrowser(
        levels={
            "root": [Entry("课程", is_dir=True)],
            "课程": [Entry("第一章", is_dir=True)],
            "课程/第一章": [Entry("01.mp4", is_dir=False)],
        }
    )

    paths = capture_directory_screenshots(
        "https://pan.quark.cn/s/new", tmp_path, browser=browser
    )

    assert browser.opened_url == "https://pan.quark.cn/s/new"
    assert browser.captured_levels == ["课程", "课程/第一章"]
    assert [path.name for path in paths] == ["directory-1.png", "directory-2.png"]


def test_capture_returns_one_image_without_deeper_directory(tmp_path: Path) -> None:
    browser = FakeShareBrowser(
        levels={
            "root": [Entry("课程", is_dir=True)],
            "课程": [Entry("01.mp4", is_dir=False)],
        }
    )

    paths = capture_directory_screenshots("share", tmp_path, browser=browser)

    assert browser.captured_levels == ["课程"]
    assert [path.name for path in paths] == ["directory-1.png"]


@pytest.mark.parametrize("content", [None, b"", b"not-a-png"])
def test_capture_rejects_missing_empty_or_invalid_png(
    tmp_path: Path,
    content: bytes | None,
) -> None:
    class BrokenCaptureBrowser(FakeShareBrowser):
        def capture(self, path: Path) -> None:
            if content is not None:
                path.write_bytes(content)

    browser = BrokenCaptureBrowser(
        levels={"root": [Entry("课程", True)], "课程": [Entry("01.mp4", False)]}
    )

    with pytest.raises(ScreenshotFailed):
        capture_directory_screenshots("share", tmp_path, browser=browser)


class FakeElement:
    def __init__(self, name: str, *, is_dir: bool, on_click=None) -> None:
        self.text = name
        self._name = name
        self._is_dir = is_dir
        self._on_click = on_click

    def attr(self, name: str):
        return {
            "data-file-name": self._name,
            "data-is-dir": "true" if self._is_dir else "false",
            "class": "folder-row" if self._is_dir else "file-row",
        }.get(name)

    def ele(self, _selector: str):
        return self

    def click(self) -> None:
        if self._on_click:
            self._on_click()


class FakePage:
    def __init__(self, selectors: DrissionPageSelectors, tmp_path: Path) -> None:
        self.selectors = selectors
        self.tmp_path = tmp_path
        self.level = "root"
        self.url = "about:blank#root"
        self.opened = None
        self.levels = {
            "root": [("课程", True)],
            "课程": [("第一章", True), ("说明.txt", False)],
            "课程/第一章": [("01.mp4", False)],
        }

    def get(self, url: str) -> None:
        self.opened = url
        self.url = f"{url}#root"

    def eles(self, selector: str):
        assert selector == self.selectors.item
        return [
            FakeElement(
                name,
                is_dir=is_dir,
                on_click=lambda name=name: self._enter(name),
            )
            for name, is_dir in self.levels[self.level]
        ]

    def ele(self, selector: str, timeout: float = 0):
        return None

    def _enter(self, name: str) -> None:
        self.level = name if self.level == "root" else f"{self.level}/{name}"
        self.url = f"https://pan.quark.cn/s/new#{self.level}"

    def back(self) -> None:
        self.level = self.level.rsplit("/", 1)[0] if "/" in self.level else "root"
        self.url = f"https://pan.quark.cn/s/new#{self.level}"

    def get_screenshot(self, *, path: str, full_page: bool) -> None:
        Image.new("RGB", (2, 2), "white").save(path, format="PNG")


def test_drission_browser_maps_entries_and_navigates_with_page_double(tmp_path: Path) -> None:
    selectors = DrissionPageSelectors()
    page = FakePage(selectors, tmp_path)
    browser = DrissionPageShareBrowser(
        page=page,
        selectors=selectors,
        timeout_seconds=0.1,
        poll_interval_seconds=0,
    )

    browser.open_share("https://pan.quark.cn/s/new")
    assert browser.list_entries() == [Entry("课程", True)]
    browser.enter_directory(Entry("课程", True))
    assert browser.list_entries() == [Entry("第一章", True), Entry("说明.txt", False)]
    browser.enter_directory(Entry("第一章", True))
    browser.leave_directory()
    assert browser.list_entries()[0] == Entry("第一章", True)
    output = tmp_path / "browser.png"
    browser.capture(output)
    assert output.is_file()


def test_drission_browser_raises_typed_failure_for_error_state(tmp_path: Path) -> None:
    selectors = DrissionPageSelectors()
    page = FakePage(selectors, tmp_path)
    page.levels["root"] = []
    page.ele = lambda selector, timeout=0: (
        object() if selector == selectors.error else None
    )
    browser = DrissionPageShareBrowser(
        page=page,
        selectors=selectors,
        timeout_seconds=0.1,
        poll_interval_seconds=0,
    )

    with pytest.raises(ScreenshotFailed, match="error"):
        browser.open_share("https://pan.quark.cn/s/new")


def test_navigation_marker_allows_identical_parent_and_child_entries(tmp_path: Path) -> None:
    selectors = DrissionPageSelectors()
    page = FakePage(selectors, tmp_path)
    page.levels = {
        "root": [("课程", True)],
        "课程": [("课程", True)],
    }
    browser = DrissionPageShareBrowser(
        page=page,
        selectors=selectors,
        timeout_seconds=0.1,
        poll_interval_seconds=0,
    )

    browser.open_share("https://pan.quark.cn/s/new")
    browser.enter_directory(Entry("课程", True))
    assert page.level == "课程"
    browser.leave_directory()
    assert page.level == "root"


def test_owned_browser_is_closed_on_success_and_failure(tmp_path: Path) -> None:
    class ClosingBrowser(FakeShareBrowser):
        def __init__(self, levels):
            super().__init__(levels)
            self.closed = 0

        def close(self):
            self.closed += 1

    success = ClosingBrowser(
        {"root": [Entry("课程", True)], "课程": [Entry("01.mp4", False)]}
    )
    capture_directory_screenshots(
        "share", tmp_path / "success", browser_factory=lambda: success
    )
    assert success.closed == 1

    failure = ClosingBrowser({"root": []})
    with pytest.raises(NoUsableScreenshot):
        capture_directory_screenshots(
            "share", tmp_path / "failure", browser_factory=lambda: failure
        )
    assert failure.closed == 1


def test_injected_browser_and_page_are_not_closed(tmp_path: Path) -> None:
    browser = FakeShareBrowser(
        {"root": [Entry("课程", True)], "课程": [Entry("01.mp4", False)]}
    )
    browser.close = lambda: pytest.fail("injected browser must not close")
    capture_directory_screenshots("share", tmp_path, browser=browser)

    selectors = DrissionPageSelectors()
    page = FakePage(selectors, tmp_path)
    page.quit = lambda: pytest.fail("injected page must not close")
    adapter = DrissionPageShareBrowser(page=page, selectors=selectors)
    adapter.close()


def test_adapter_closes_page_created_by_page_factory(tmp_path: Path) -> None:
    selectors = DrissionPageSelectors()
    page = FakePage(selectors, tmp_path)
    closed = []
    page.quit = lambda: closed.append(True)
    adapter = DrissionPageShareBrowser(
        page_factory=lambda: page,
        selectors=selectors,
    )

    with adapter:
        pass

    assert closed == [True]


def test_capture_rejects_share_without_non_root_directory(tmp_path: Path) -> None:
    browser = FakeShareBrowser(
        levels={"root": [Entry("README.txt", is_dir=False)]}
    )

    with pytest.raises(NoUsableScreenshot):
        capture_directory_screenshots("share", tmp_path, browser=browser)

    assert browser.captured_levels == []


def test_capture_ignores_parent_navigation_pseudo_directory(tmp_path: Path) -> None:
    browser = FakeShareBrowser(
        levels={
            "root": [Entry("..", is_dir=True), Entry("课程", is_dir=True)],
            "课程": [Entry("01.mp4", is_dir=False)],
        }
    )

    capture_directory_screenshots("share", tmp_path, browser=browser)

    assert browser.captured_levels == ["课程"]


def test_resource_bundle_requires_one_directory_and_two_content_images(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory.png"
    content_1 = tmp_path / "content-1.png"
    content_2 = tmp_path / "content-2.png"
    for path, color in (
        (directory, "red"),
        (content_1, "green"),
        (content_2, "blue"),
    ):
        Image.new("RGB", (40, 30), color).save(path, format="PNG")

    bundle = ResourceScreenshotBundle(directory, (content_1, content_2))

    assert validate_resource_screenshot_bundle(bundle) == bundle


@pytest.mark.parametrize("content_count", [0, 1])
def test_resource_bundle_rejects_fewer_than_two_content_images(
    tmp_path: Path, content_count: int
) -> None:
    directory = tmp_path / "directory.png"
    Image.new("RGB", (40, 30), "red").save(directory, format="PNG")
    contents = []
    for index in range(content_count):
        path = tmp_path / f"content-{index + 1}.png"
        Image.new("RGB", (40, 30), "green").save(path, format="PNG")
        contents.append(path)

    with pytest.raises(ScreenshotFailed, match="two content"):
        validate_resource_screenshot_bundle(
            ResourceScreenshotBundle(directory, tuple(contents))
        )


def test_resource_bundle_rejects_directory_reused_as_content(tmp_path: Path) -> None:
    directory = tmp_path / "directory.png"
    content = tmp_path / "content.png"
    Image.new("RGB", (40, 30), "red").save(directory, format="PNG")
    Image.new("RGB", (40, 30), "green").save(content, format="PNG")

    with pytest.raises(ScreenshotFailed, match="distinct"):
        validate_resource_screenshot_bundle(
            ResourceScreenshotBundle(directory, (directory, content))
        )


def test_resource_bundle_rejects_duplicate_content_pixels(tmp_path: Path) -> None:
    directory = tmp_path / "directory.png"
    content_1 = tmp_path / "content-1.png"
    content_2 = tmp_path / "content-2.png"
    Image.new("RGB", (40, 30), "red").save(directory, format="PNG")
    Image.new("RGB", (40, 30), "white").save(content_1, format="PNG")
    content_2.write_bytes(content_1.read_bytes())

    with pytest.raises(ScreenshotFailed, match="duplicate"):
        validate_resource_screenshot_bundle(
            ResourceScreenshotBundle(directory, (content_1, content_2))
        )


def test_capture_resource_screenshots_returns_one_directory_and_two_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "shots"
    output.mkdir()
    directory = output / "directory.png"
    content_1 = output / "content-1.png"
    content_2 = output / "content-2.png"
    for path, color in (
        (directory, "red"),
        (content_1, "green"),
        (content_2, "blue"),
    ):
        Image.new("RGB", (80, 60), color).save(path, format="PNG")

    monkeypatch.setattr(
        "quarkmover.screenshots._resolve_public_resource_route",
        lambda _url: PublicResourceRoute(
            ("inner-fid",), ("https://preview.test/one", "https://preview.test/two")
        ),
    )
    calls = []

    def run_helper(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "directory": str(directory),
                    "contents": [str(content_1), str(content_2)],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("quarkmover.screenshots.subprocess.run", run_helper)

    bundle = capture_resource_screenshots(
        "https://pan.quark.cn/s/Abc123", output
    )

    assert bundle.directory == directory
    assert bundle.contents == (content_1, content_2)
    assert calls[0][0][-1] == "inner-fid"
    assert calls[0][1]["timeout"] == 180
    assert calls[0][1]["text"] is False
    assert json.loads(calls[0][1]["input"].decode("utf-8")) == {
        "previewUrls": ["https://preview.test/one", "https://preview.test/two"]
    }


def test_capture_resource_screenshots_rejects_missing_inner_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "quarkmover.screenshots._resolve_public_resource_route",
        lambda _url: PublicResourceRoute((), ()),
    )

    with pytest.raises(NoUsableScreenshot, match="non-root"):
        capture_resource_screenshots(
            "https://pan.quark.cn/s/Abc123", tmp_path
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"directory": "only-directory.png", "contents": []},
        {"directory": "same.png", "contents": ["same.png", "other.png"]},
    ],
)
def test_capture_resource_screenshots_rejects_invalid_helper_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr(
        "quarkmover.screenshots._resolve_public_resource_route",
        lambda _url: PublicResourceRoute(
            ("inner-fid",), ("https://preview.test/one", "https://preview.test/two")
        ),
    )
    monkeypatch.setattr(
        "quarkmover.screenshots.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(payload), stderr=""
        ),
    )

    with pytest.raises(ScreenshotFailed):
        capture_resource_screenshots(
            "https://pan.quark.cn/s/Abc123", tmp_path
        )


def test_capture_resource_screenshots_rejects_helper_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "quarkmover.screenshots._resolve_public_resource_route",
        lambda _url: PublicResourceRoute(
            ("inner-fid",), ("https://preview.test/one", "https://preview.test/two")
        ),
    )
    monkeypatch.setattr(
        "quarkmover.screenshots.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="private browser detail"
        ),
    )

    with pytest.raises(ScreenshotFailed, match="helper failed"):
        capture_resource_screenshots(
            "https://pan.quark.cn/s/Abc123", tmp_path
        )


def test_resolve_public_directory_fids_uses_public_api_without_legacy_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return Response({"status": 200, "data": {"stoken": "safe-token"}})

        def get(self, url, **kwargs):
            parent = kwargs["params"]["pdir_fid"]
            calls.append(("get", url, parent))
            if parent == "0":
                return Response({"status": 200, "data": {"list": [
                    {"dir": True, "fid": "inner-fid"}
                ]}})
            return Response({"status": 200, "data": {"list": [
                {"dir": True, "fid": "nested-fid"},
                {"dir": False, "fid": "file-1", "big_thumbnail": "https://preview.test/one"},
                {"dir": False, "fid": "file-2", "thumbnail": "https://preview.test/two"},
            ]}})

    monkeypatch.setattr("quarkmover.screenshots.httpx.Client", Client)

    route = _resolve_public_resource_route("https://pan.quark.cn/s/Abc123")
    assert route.fids == ("inner-fid",)
    assert route.preview_urls == (
        "https://preview.test/one", "https://preview.test/two"
    )
    assert [item[0] for item in calls] == ["client", "post", "get", "get"]


def test_content_capture_helper_uses_declared_local_playwright_dependency() -> None:
    module_root = Path(__file__).resolve().parents[1]
    source = (
        module_root / "quarkmover" / "playwright_capture_content.js"
    ).read_text(encoding="utf-8")
    package = json.loads((module_root / "package.json").read_text(encoding="utf-8"))

    assert "require('playwright')" in source
    assert "nvm4w" not in source
    assert "locator('img:visible')" in source
    assert package["dependencies"]["playwright"]


def test_preview_urls_prefer_larger_content_files() -> None:
    assert _entry_preview_urls([
        {"dir": False, "size": 2, "big_thumbnail": "https://preview.test/tiny"},
        {"dir": False, "size": 50, "big_thumbnail": "https://preview.test/large"},
        {"dir": False, "size": 20, "thumbnail": "https://preview.test/medium"},
    ]) == (
        "https://preview.test/large",
        "https://preview.test/medium",
        "https://preview.test/tiny",
    )


def test_content_selection_rejects_blank_preview_frames(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    pattern_1 = tmp_path / "pattern-1.png"
    pattern_2 = tmp_path / "pattern-2.png"
    Image.new("RGB", (120, 80), "white").save(blank)
    first = Image.new("RGB", (120, 80), "white")
    second = Image.new("RGB", (120, 80), "white")
    for x in range(0, 120, 8):
        for y in range(0, 80, 8):
            first.putpixel((x, y), (0, 0, 0))
            second.putpixel((x, y), (255, 0, 0) if (x + y) % 16 else (0, 0, 255))
    first.save(pattern_1)
    second.save(pattern_2)

    selected = _select_content_screenshots((blank, pattern_1, pattern_2))
    assert set(selected) == {pattern_1, pattern_2}


def test_entry_preview_urls_can_disable_risk_word_filtering() -> None:
    entries = [
        {
            "dir": False,
            "file_name": "品牌名称版资料.pdf",
            "size": 100,
            "preview_url": "https://preview.test/brand",
        }
    ]

    assert _entry_preview_urls(entries, filter_blocked_terms=False) == (
        "https://preview.test/brand",
    )
