from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Protocol

from PIL import Image, ImageStat


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_QUARKMOVER_SOURCE = _REPOSITORY_ROOT / "quarkmover"
if _QUARKMOVER_SOURCE.is_dir():
    sys.path.insert(0, str(_QUARKMOVER_SOURCE))


_QUARK_SHARE = re.compile(r"https://pan\.quark\.cn/s/[A-Za-z0-9]+\Z")


class ContentUnavailable(RuntimeError):
    """The share has no two usable previewable content files."""


class CaptureBackend(Protocol):
    def capture_content(self, url: str, output: Path) -> dict[str, Any]: ...
    def capture_inner_directories(self, url: str, output: Path) -> dict[str, Any]: ...


class QuarkCaptureBackend:
    """Adapter around the repository's tested public-share capture library."""

    def capture_content(self, url: str, output: Path) -> dict[str, Any]:
        from quarkmover.screenshots import (
            NoUsableScreenshot,
            capture_resource_screenshots,
        )

        try:
            bundle = capture_resource_screenshots(
                url, output, filter_blocked_terms=False
            )
        except NoUsableScreenshot as error:
            raise ContentUnavailable(str(error)) from error
        return {
            "directory": bundle.directory,
            "contents": bundle.contents[:2],
            "sources": tuple({"depth": 1} for _ in range(3)),
        }

    def capture_inner_directories(self, url: str, output: Path) -> dict[str, Any]:
        from quarkmover.screenshots import capture_directory_screenshots

        directories = capture_directory_screenshots(
            url, output, filter_blocked_terms=False
        )
        if len(directories) < 2:
            raise ContentUnavailable("fewer than two inner-directory screenshots")
        return {
            "directories": directories[:2],
            "sources": ({"depth": 1}, {"depth": 1}),
        }


def validate_share_url(value: str) -> str:
    if not isinstance(value, str) or _QUARK_SHARE.fullmatch(value.strip()) is None:
        raise ValueError("url must be a public Quark share URL")
    return value.strip()


def validate_output_directory(value: Path) -> Path:
    output = Path(value).resolve()
    if output.exists() and not output.is_dir():
        raise ValueError("output must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _copy(source: Path, destination: Path) -> Path:
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError("captured screenshot is missing")
    if source != destination.resolve():
        shutil.copyfile(source, destination)
    return destination.resolve()


def _image_info(path: Path) -> dict[str, int]:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError("captured screenshot must be PNG")
            width, height = image.size
            gray = image.convert("L")
            gray.thumbnail((320, 240), Image.Resampling.LANCZOS)
            if gray.entropy() < 0.5 or ImageStat.Stat(gray).stddev[0] < 1.0:
                raise ValueError("captured screenshot is blank")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("captured screenshot is invalid") from error
    if width < 640 or height < 360:
        raise ValueError("captured screenshot resolution is too small")
    return {"width": width, "height": height}


def _validate_distinct(paths: list[Path]) -> None:
    digests = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    if len(set(digests)) != len(digests):
        raise ValueError("selected screenshots must be distinct")


def capture(
    url: str,
    output: Path,
    *,
    backend: CaptureBackend | None = None,
) -> dict[str, object]:
    share_url = validate_share_url(url)
    output_dir = validate_output_directory(output)
    active_backend = backend or QuarkCaptureBackend()
    fallback_reason: str | None = None
    try:
        raw = active_backend.capture_content(share_url, output_dir)
        sources = list(raw.get("sources") or ())
        selected = [
            _copy(Path(raw["directory"]), output_dir / "directory.png"),
            *[
                _copy(Path(path), output_dir / f"content-{index}.png")
                for index, path in enumerate(tuple(raw["contents"])[:2], 1)
            ],
        ]
        if len(selected) != 3:
            raise ContentUnavailable("fewer than two usable content screenshots")
        mode = "content"
    except ContentUnavailable as error:
        fallback_reason = str(error)
        raw = active_backend.capture_inner_directories(share_url, output_dir)
        sources = list(raw.get("sources") or ())
        selected = [
            _copy(Path(path), output_dir / f"directory-{index}.png")
            for index, path in enumerate(tuple(raw["directories"])[:2], 1)
        ]
        if len(selected) != 2 or any(int(source.get("depth", 0)) < 1 for source in sources):
            raise ValueError("fallback requires two inner-directory screenshots")
        mode = "directory-only"

    _validate_distinct(selected)
    images = [dict(path=str(path), **_image_info(path)) for path in selected]
    result: dict[str, object] = {
        "version": "无风险词控制版",
        "mode": mode,
        "share_url": share_url,
        "screenshots": [str(path) for path in selected],
        "images": images,
        "sources": sources,
        "risk_word_control": False,
        "ocr_reviewed": False,
    }
    if fallback_reason is not None:
        result["fallback_reason"] = fallback_reason
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture real content or inner-directory screenshots from a public Quark share."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chrome-path")
    args = parser.parse_args(argv)
    if args.chrome_path:
        os.environ["CHROME_PATH"] = args.chrome_path
    try:
        result = capture(args.url, args.output)
    except Exception as error:
        print(f"capture failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
