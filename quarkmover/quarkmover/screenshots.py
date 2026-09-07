from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import httpx
import json
from pathlib import Path
import random
import stat
import time
import re
import subprocess
from typing import Any, Mapping, Protocol

from PIL import Image, ImageStat


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    fid: str | None = None


@dataclass(frozen=True)
class ResourceScreenshotBundle:
    directory: Path
    contents: tuple[Path, ...]


@dataclass(frozen=True)
class PublicResourceRoute:
    fids: tuple[str, ...]
    preview_urls: tuple[str, ...]


_SCREENSHOT_BLOCKED_TERMS = (
    "waterinbullrun.com", "更多优质资源", "更多资源", "公众号", "推广",
    "人教版", "人教", "部编版", "小状元", "学而思", "高途", "新东方",
    "清华附小", "万象思维", "高考快递", "山东省", "广东省",
    "深圳", "名校",
)


def _directory_entries_are_clean(entries: Sequence[Mapping[str, Any]]) -> bool:
    return not any(
        term in str(entry.get("file_name") or "")
        for entry in entries for term in _SCREENSHOT_BLOCKED_TERMS
    )


class ShareBrowser(Protocol):
    def open_share(self, share_url: str) -> None: ...
    def list_entries(self) -> Sequence[Entry]: ...
    def enter_directory(self, entry: Entry) -> None: ...
    def leave_directory(self) -> None: ...
    def capture(self, path: Path) -> None: ...


class ScreenshotFailed(RuntimeError):
    """A browser state or captured image was unusable."""


class NoUsableScreenshot(ScreenshotFailed):
    """The public share contains no non-root directory that can be captured."""


@dataclass(frozen=True)
class DrissionPageSelectors:
    item: str = "css:[data-testid='share-file-item'], div[class*='file-item']"
    name: str = "css:[data-testid='file-name'], [class*='file-name']"
    empty: str = "css:[data-testid='empty-state'], [class*='empty']"
    error: str = "css:[data-testid='error-state'], [class*='error']"
    breadcrumb: str = "css:[data-testid='breadcrumb'], [class*='breadcrumb']"


class DrissionPageShareBrowser:
    """DrissionPage adapter for public Quark shares; page may be injected offline."""

    def __init__(
        self,
        *,
        page: Any | None = None,
        page_factory: Callable[[], Any] | None = None,
        selectors: DrissionPageSelectors | None = None,
        timeout_seconds: float = 15,
        poll_interval_seconds: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0 or poll_interval_seconds < 0:
            raise ValueError("browser wait settings are invalid")
        if page is None:
            if page_factory is None:
                from DrissionPage import ChromiumPage

                page_factory = ChromiumPage
            page = page_factory()
            self._owns_page = True
        else:
            self._owns_page = False
        self._page = page
        self._selectors = selectors or DrissionPageSelectors()
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def __enter__(self) -> "DrissionPageShareBrowser":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._owns_page:
            return
        try:
            self._page.quit()
        except Exception as error:
            raise ScreenshotFailed(
                f"failed to close browser: {type(error).__name__}"
            ) from error

    def open_share(self, share_url: str) -> None:
        try:
            self._page.get(share_url)
            self._wait_for_entries()
        except ScreenshotFailed:
            raise
        except Exception as error:
            raise ScreenshotFailed(
                f"failed to open public share: {type(error).__name__}"
            ) from error

    def open_directory_fid(self, fid: str) -> None:
        """Navigate using the stable Quark public-share directory route."""
        if not isinstance(fid, str) or not fid.strip():
            raise ScreenshotFailed("directory fid is invalid")
        current = str(getattr(self._page, "url", "") or "")
        base = current.split("#", 1)[0]
        if not base:
            raise ScreenshotFailed("share base URL is unavailable")
        try:
            self._page.get(f"{base}#/list/share/{fid}")
            run_js = getattr(self._page, "run_js", None)
            if callable(run_js):
                run_js(f"window.location.hash = '#/list/share/{fid}'")
            self._sleep(max(self._poll_interval_seconds, 10.0))
            self._wait_for_entries()
        except ScreenshotFailed:
            raise
        except Exception as error:
            raise ScreenshotFailed(
                f"failed to open directory route: {type(error).__name__}"
            ) from error

    def list_entries(self) -> list[Entry]:
        return self._wait_for_entries()

    def enter_directory(self, entry: Entry) -> None:
        if not _is_real_directory(entry):
            raise ScreenshotFailed("entry is not a real directory")
        try:
            previous_entries = self._entries_now()
            previous_marker = self._navigation_marker()
            element = next(
                (
                    element
                    for element in self._item_elements()
                    if self._entry_from_element(element) == entry
                ),
                None,
            )
            if element is None:
                raise ScreenshotFailed(f"directory is no longer available: {entry.name}")
            current_url = str(getattr(self._page, "url", "") or "")
            clicker = element.click
            multi_click = getattr(clicker, "multi", None)
            # Prefer a JS-backed click for the virtualized Quark row.  Some
            # public-share builds ignore DrissionPage's synthetic double-click
            # on the row while still responding to a real DOM click.
            try:
                clicker(by_js=True)
            except TypeError:
                clicker()
            if callable(multi_click):
                self._sleep(max(self._poll_interval_seconds, 0.3))
                current_after_click = str(getattr(self._page, "url", "") or "")
                if current_after_click == current_url:
                    multi_click(2)
            # Quark's public-share UI can acknowledge a double click without
            # changing the hash route.  The working thread's capture flow
            # navigates by the directory fid, so use that same route as a
            # deterministic fallback/confirmation when the page exposes it.
            fid = getattr(entry, "fid", None)
            if isinstance(fid, str) and fid.strip():
                current_url = str(getattr(self._page, "url", "") or "")
                if f"#/list/share/{fid}" not in current_url:
                    base = current_url.split("#", 1)[0] or str(getattr(self._page, "url", "") or "")
                    self._page.get(f"{base}#/list/share/{fid}")
            # The public page updates its route before the directory rows are
            # refreshed.  Give that refresh a short settling window so the
            # capture cannot accidentally record the previous (root) list.
            # Match the established capture script's settle window: Quark
            # paints the new directory asynchronously after changing the
            # route, and an early screenshot can still show the share root.
            self._sleep(max(self._poll_interval_seconds, 10.0))
            self._wait_for_entries(
                previous_entries=previous_entries,
                previous_marker=previous_marker,
            )
            final_url = str(getattr(self._page, "url", "") or "")
            if "pan.quark.cn" in final_url and (
                final_url.endswith("#/list/share/0")
                or re.search(r"/s/[A-Za-z0-9_-]+$", final_url) is not None
            ):
                raise NoUsableScreenshot("directory navigation remained at share root")
        except ScreenshotFailed:
            raise
        except Exception as error:
            raise ScreenshotFailed(
                f"failed to enter directory: {type(error).__name__}"
            ) from error

    def leave_directory(self) -> None:
        try:
            previous_entries = self._entries_now()
            previous_marker = self._navigation_marker()
            self._page.back()
            self._wait_for_entries(
                previous_entries=previous_entries,
                previous_marker=previous_marker,
            )
        except ScreenshotFailed:
            raise
        except Exception as error:
            raise ScreenshotFailed(
                f"failed to leave directory: {type(error).__name__}"
            ) from error

    def capture(self, path: Path) -> None:
        try:
            self._page.get_screenshot(path=str(path), full_page=False)
            _verify_png(path)
        except ScreenshotFailed:
            raise
        except Exception as error:
            raise ScreenshotFailed(
                f"browser screenshot failed: {type(error).__name__}"
            ) from error

    def _wait_for_entries(
        self,
        *,
        previous_entries: list[Entry] | None = None,
        previous_marker: tuple[str, ...] | None = None,
    ) -> list[Entry]:
        deadline = self._monotonic() + self._timeout_seconds
        while True:
            if self._page.ele(self._selectors.error, timeout=0):
                raise ScreenshotFailed("public share displayed an error state")
            entries = self._entries_now()
            marker = self._navigation_marker()
            navigation_changed = (
                previous_marker is not None and marker != previous_marker
            )
            entries_changed = (
                previous_entries is not None and entries != previous_entries
            )
            if entries and (
                previous_entries is None or navigation_changed or entries_changed
            ):
                return entries
            if self._page.ele(self._selectors.empty, timeout=0):
                return []
            if self._monotonic() >= deadline:
                raise ScreenshotFailed("timed out waiting for share directory")
            self._sleep(self._poll_interval_seconds)

    def _item_elements(self) -> Sequence[Any]:
        return self._page.eles(self._selectors.item) or []

    def _entries_now(self) -> list[Entry]:
        return [
            entry
            for element in self._item_elements()
            if (entry := self._entry_from_element(element)) is not None
        ]

    def _navigation_marker(self) -> tuple[str, ...]:
        values = [str(getattr(self._page, "url", "") or "")]
        breadcrumb = self._page.ele(self._selectors.breadcrumb, timeout=0)
        if breadcrumb:
            values.append(str(getattr(breadcrumb, "text", "") or ""))
            values.append(str(breadcrumb.attr("data-fid") or ""))
        title = getattr(self._page, "title", "")
        if title:
            values.append(str(title))
        return tuple(value for value in values if value)

    def _entry_from_element(self, element: Any) -> Entry | None:
        name = element.attr("data-file-name")
        if not name:
            name_element = element.ele(self._selectors.name)
            name = getattr(name_element, "text", "") if name_element else ""
        name = str(name or "").strip()
        if not name:
            return None
        is_dir_value = str(element.attr("data-is-dir") or "").casefold()
        file_type = str(element.attr("data-file-type") or "").casefold()
        class_name = str(element.attr("class") or "").casefold()
        is_dir = (
            is_dir_value in {"1", "true", "yes"}
            or file_type in {"dir", "directory", "folder"}
            or "folder" in class_name
            or "directory" in class_name
        )
        fid = None
        for attr_name in ("data-fid", "fid", "data-id", "data-file-id"):
            fid = element.attr(attr_name)
            if fid:
                break
        if not fid:
            name_element = element.ele(self._selectors.name)
            if name_element:
                for attr_name in ("data-fid", "fid", "data-id", "data-file-id"):
                    fid = name_element.attr(attr_name)
                    if fid:
                        break
        if not fid:
            attrs = getattr(element, "attrs", None)
            if callable(attrs):
                attrs = attrs()
            if isinstance(attrs, Mapping):
                for attr_name in ("data-fid", "fid", "data-id", "data-file-id"):
                    if attrs.get(attr_name):
                        fid = attrs[attr_name]
                        break
        if not fid:
            for candidate in (
                element.attr("href"),
                getattr(element, "html", ""),
            ):
                text = str(candidate or "")
                patterns = (
                    r"#/list/share/([A-Za-z0-9_-]+)",
                    r"data-(?:fid|file-id|id)=[\"']([^\"']+)",
                    r"[\"'](?:fid|file_id|fileId|id)[\"']\s*:\s*[\"']([^\"']+)",
                )
                for pattern in patterns:
                    match = re.search(pattern, text)
                    if match:
                        fid = match.group(1)
                        break
                if fid:
                    break
        return Entry(name=name, is_dir=is_dir, fid=str(fid).strip() if fid else None)


def _is_real_directory(entry: Entry) -> bool:
    return entry.is_dir and entry.name.strip() not in {"", ".", ".."}


def _verify_png(path: Path) -> None:
    if (
        not path.exists()
        or not stat.S_ISREG(path.lstat().st_mode)
        or path.stat().st_size == 0
    ):
        raise ScreenshotFailed("screenshot file is missing or empty")
    try:
        with Image.open(path) as image:
            image.verify()
            if image.format != "PNG":
                raise ScreenshotFailed("screenshot is not a PNG image")
    except ScreenshotFailed:
        raise
    except Exception as error:
        raise ScreenshotFailed("screenshot image is invalid") from error


def validate_resource_screenshot_bundle(
    bundle: ResourceScreenshotBundle,
) -> ResourceScreenshotBundle:
    if not isinstance(bundle, ResourceScreenshotBundle):
        raise ScreenshotFailed("resource screenshot bundle is invalid")
    if not isinstance(bundle.directory, Path):
        raise ScreenshotFailed("directory screenshot path is invalid")
    _verify_png(bundle.directory)
    if not isinstance(bundle.contents, tuple) or len(bundle.contents) < 2:
        raise ScreenshotFailed("at least two content screenshots are required")
    if not all(isinstance(path, Path) for path in bundle.contents):
        raise ScreenshotFailed("content screenshot path is invalid")

    resolved = [bundle.directory.resolve()]
    for path in bundle.contents:
        _verify_png(path)
        resolved.append(path.resolve())
    if len(set(resolved)) != len(resolved):
        raise ScreenshotFailed(
            "directory and content screenshots must be distinct"
        )
    content_hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in bundle.contents
    }
    if len(content_hashes) != len(bundle.contents):
        raise ScreenshotFailed("duplicate content screenshots are not allowed")
    return bundle


def _capture_verified(browser: ShareBrowser, path: Path) -> None:
    try:
        browser.capture(path)
        _verify_png(path)
    except ScreenshotFailed:
        raise
    except Exception as error:
        raise ScreenshotFailed(
            f"screenshot capture failed: {type(error).__name__}"
        ) from error


def capture_directory_screenshots(
    share_url: str,
    output_dir: Path,
    *,
    browser: ShareBrowser | None = None,
    browser_factory: Callable[[], ShareBrowser] = DrissionPageShareBrowser,
) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    owns_browser = browser is None
    active_browser = browser
    try:
        if active_browser is None:
            active_browser = browser_factory()
        active_browser.open_share(share_url)
        route_fids = _resolve_public_directory_fids(share_url)
        if route_fids and active_browser.__class__ is DrissionPageShareBrowser:
            _capture_with_playwright(share_url, output_dir, route_fids)
            return tuple(output_dir / f"directory-{index}.png" for index in (1, 2))
        if route_fids and hasattr(active_browser, "open_directory_fid"):
            active_browser.open_directory_fid(route_fids[0])
            first_path = output_dir / "directory-1.png"
            _capture_verified(active_browser, first_path)
            paths = [first_path]
            if len(route_fids) > 1:
                active_browser.open_directory_fid(route_fids[1])
                second_path = output_dir / "directory-2.png"
                _capture_verified(active_browser, second_path)
                paths.append(second_path)
            return tuple(paths)
        root_entries = list(active_browser.list_entries())
        root_directory = next(
            (entry for entry in root_entries if _is_real_directory(entry)),
            None,
        )
        if root_directory is None:
            raise NoUsableScreenshot("share has no non-root directory")

        active_browser.enter_directory(root_directory)
        inner_entries = list(active_browser.list_entries())
        if inner_entries == root_entries:
            raise NoUsableScreenshot("directory navigation did not leave share root")
        first_path = output_dir / "directory-1.png"
        _capture_verified(active_browser, first_path)
        paths = [first_path]

        child_directories = [
            entry for entry in active_browser.list_entries() if _is_real_directory(entry)
        ]
        for child in child_directories:
            active_browser.enter_directory(child)
            child_entries = active_browser.list_entries()
            if any(not entry.is_dir for entry in child_entries):
                second_path = output_dir / "directory-2.png"
                _capture_verified(active_browser, second_path)
                paths.append(second_path)
                return tuple(paths)
            active_browser.leave_directory()
        return tuple(paths)
    except ScreenshotFailed:
        raise
    except Exception as error:
        raise ScreenshotFailed(
            f"screenshot workflow failed: {type(error).__name__}"
        ) from error
    finally:
        if owns_browser and active_browser is not None:
            close = getattr(active_browser, "close", None)
            if callable(close):
                close()


def capture_resource_screenshots(
    share_url: str,
    output_dir: Path,
) -> ResourceScreenshotBundle:
    route = _resolve_public_resource_route(share_url)
    if not route.fids:
        raise NoUsableScreenshot("share has no non-root directory")
    if len(route.preview_urls) < 2:
        raise NoUsableScreenshot("share has fewer than two previewable content files")
    output_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("playwright_capture_content.js")
    try:
        completed = subprocess.run(
            ["node", str(script), share_url, str(output_dir), *route.fids],
            capture_output=True,
            text=False,
            input=json.dumps(
                {"previewUrls": list(route.preview_urls)},
                ensure_ascii=False,
            ).encode("utf-8"),
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScreenshotFailed(
            f"resource screenshot helper failed: {type(error).__name__}"
        ) from None
    if completed.returncode != 0:
        detail = _subprocess_text(completed.stderr).strip()
        raise ScreenshotFailed(f"resource screenshot helper failed: {detail or 'unknown error'}")
    try:
        stdout = completed.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="strict")
        payload = json.loads(stdout)
        directory = Path(payload["directory"])
        contents = _select_content_screenshots(
            tuple(Path(path) for path in payload["contents"])
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ScreenshotFailed(
            "resource screenshot helper returned invalid output"
        ) from None
    return validate_resource_screenshot_bundle(
        ResourceScreenshotBundle(directory, contents)
    )


def _resolve_public_directory_fids(share_url: str) -> tuple[str, ...]:
    return _resolve_public_resource_route(share_url).fids


def _resolve_public_resource_route(share_url: str) -> PublicResourceRoute:
    """Resolve a content-bearing inner folder and its real preview images."""
    try:
        match = re.fullmatch(
            r"https://pan\.quark\.cn/s/([A-Za-z0-9]+)", share_url.strip()
        )
        if match is None:
            return PublicResourceRoute((), ())
        pwd_id = match.group(1)
        headers = {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            "origin": "https://pan.quark.cn",
            "referer": "https://pan.quark.cn/",
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
        }
        with httpx.Client(headers=headers, trust_env=True, timeout=30) as client:
            token_payload = client.post(
                "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
                params=_public_quark_params(),
                json={"pwd_id": pwd_id, "passcode": ""},
            ).json()
            stoken = (token_payload.get("data") or {}).get("stoken")
            if token_payload.get("status") != 200 or not stoken:
                return PublicResourceRoute((), ())
            entries = _public_share_entries(client, pwd_id, str(stoken), "0")
            folders = [entry for entry in entries if entry.get("dir") and entry.get("fid")]
            if not folders:
                return PublicResourceRoute((), ())
            root_fids = [str(folder["fid"]) for folder in folders[:20]]
            root_entries = [
                _public_share_entries(client, pwd_id, str(stoken), fid)
                for fid in root_fids
            ]
            queue: list[tuple[str, list[dict[str, Any]], int]] = [
                (fid, current, 1)
                for fid, current in zip(root_fids, root_entries)
            ]
            best = PublicResourceRoute(
                (root_fids[0],), _entry_preview_urls(root_entries[0])[:8]
            )
            visited = set(root_fids)
            checked = 0
            while queue and checked < 80:
                fid, current, depth = queue.pop(0)
                checked += 1
                previews = _entry_preview_urls(current)
                if _directory_entries_are_clean(current) and len(previews) > len(best.preview_urls):
                    best = PublicResourceRoute((fid,), previews[:8])
                if len(previews) >= 2 and _directory_entries_are_clean(current):
                    return PublicResourceRoute((fid,), previews[:8])
                if depth >= 7:
                    continue
                for entry in current:
                    nested = str(entry.get("fid") or "")
                    if not entry.get("dir") or not nested or nested in visited:
                        continue
                    visited.add(nested)
                    queue.append((
                        nested,
                        _public_share_entries(client, pwd_id, str(stoken), nested),
                        depth + 1,
                    ))
            return best
    except Exception:
        return PublicResourceRoute((), ())


def _entry_preview_urls(entries: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    urls: list[str] = []
    files = [
        entry for entry in entries
        if not entry.get("dir")
        and not any(
            term in str(entry.get("file_name") or "") for term in _SCREENSHOT_BLOCKED_TERMS
        )
    ]
    files.sort(
        key=lambda entry: entry.get("size")
        if isinstance(entry.get("size"), (int, float)) else 0,
        reverse=True,
    )
    for entry in files:
        for field in ("big_thumbnail", "thumbnail", "preview_url"):
            value = entry.get(field)
            if (
                isinstance(value, str)
                and value.startswith("https://")
                and value not in urls
            ):
                urls.append(value)
                break
    return tuple(urls)


def _select_content_screenshots(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    if len(paths) < 2:
        raise ScreenshotFailed("at least two content screenshots are required")

    def score(path: Path) -> float:
        try:
            with Image.open(path) as image:
                gray = image.convert("L")
                gray.thumbnail((320, 240), Image.Resampling.LANCZOS)
                deviation = ImageStat.Stat(gray).stddev[0]
                return gray.entropy() * 10 + deviation
        except Exception as error:
            raise ScreenshotFailed("content screenshot image is invalid") from error

    ranked = sorted(paths, key=score, reverse=True)
    selected: list[Path] = []
    hashes: set[str] = set()
    for path in ranked:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        selected.append(path)
        if len(selected) == 2:
            return tuple(selected)
    raise ScreenshotFailed("fewer than two distinct content screenshots were captured")


def _public_quark_params(extra: Mapping[str, object] | None = None) -> dict[str, object]:
    params: dict[str, object] = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    if extra:
        params.update(extra)
    params["__dt"] = random.randint(100, 9999)
    params["__t"] = int(time.time() * 1000)
    return params


def _public_share_entries(
    client: httpx.Client, pwd_id: str, stoken: str, parent_fid: str
) -> list[dict[str, Any]]:
    payload = client.get(
        "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail",
        params=_public_quark_params({
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": parent_fid,
            "force": "0",
            "_page": 1,
            "_size": 100,
            "_fetch_banner": 0,
            "_fetch_share": 0,
            "_fetch_total": 1,
            "_sort": "file_type:asc,updated_at:desc",
        }),
    ).json()
    if payload.get("status") != 200:
        return []
    entries = (payload.get("data") or {}).get("list") or []
    return entries if isinstance(entries, list) else []


def _capture_with_playwright(share_url: str, output_dir: Path, fids: tuple[str, ...]) -> None:
    script = Path(__file__).parent.parent / "playwright_capture.js"
    try:
        completed = subprocess.run(
            ["node", str(script), share_url, str(output_dir), *fids],
            capture_output=True, text=False, timeout=150, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScreenshotFailed(f"Playwright screenshot failed: {type(error).__name__}") from None
    if completed.returncode != 0:
        detail = _subprocess_text(completed.stderr).strip()
        raise ScreenshotFailed(f"Playwright screenshot failed: {detail or 'unknown error'}")
    for index in (1, 2):
        _verify_png(output_dir / f"directory-{index}.png")


def _subprocess_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""
