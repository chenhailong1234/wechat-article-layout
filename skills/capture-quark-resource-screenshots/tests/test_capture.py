from __future__ import annotations

import json
from pathlib import Path
import sys

from PIL import Image
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from capture import capture  # noqa: E402


VALID_URL = "https://pan.quark.cn/s/Example123"


def _png(path: Path, color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 600), "white")
    for x in range(0, 900, 12):
        for y in range(0, 600, 12):
            image.putpixel((x, y), color)
    image.save(path, format="PNG")
    return path


class FakeBackend:
    def __init__(self, *, content: bool = True) -> None:
        self.content = content

    def capture_content(self, _url: str, output: Path):
        if not self.content:
            from capture import ContentUnavailable

            raise ContentUnavailable("no previewable content")
        return {
            "directory": _png(output / "raw-directory.png", (30, 90, 160)),
            "contents": (
                _png(output / "raw-content-a.png", (210, 40, 40)),
                _png(output / "raw-content-b.png", (40, 180, 80)),
            ),
            "sources": ({"depth": 2}, {"depth": 2}, {"depth": 2}),
        }

    def capture_inner_directories(self, _url: str, output: Path):
        return {
            "directories": (
                _png(output / "raw-inner-a.png", (30, 90, 160)),
                _png(output / "raw-inner-b.png", (160, 90, 30)),
            ),
            "sources": ({"depth": 1}, {"depth": 2}),
        }


def test_rejects_non_quark_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="public Quark share URL"):
        capture("https://example.com/file", tmp_path, backend=FakeBackend())


def test_content_mode_writes_stable_result(tmp_path: Path) -> None:
    result = capture(VALID_URL, tmp_path, backend=FakeBackend())

    assert result["mode"] == "content"
    assert [Path(path).name for path in result["screenshots"]] == [
        "directory.png",
        "content-1.png",
        "content-2.png",
    ]
    assert result["risk_word_control"] is False
    assert result["ocr_reviewed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == result


def test_falls_back_to_two_inner_directory_images(tmp_path: Path) -> None:
    result = capture(VALID_URL, tmp_path, backend=FakeBackend(content=False))

    assert result["mode"] == "directory-only"
    assert [Path(path).name for path in result["screenshots"]] == [
        "directory-1.png",
        "directory-2.png",
    ]
    assert all(source["depth"] >= 1 for source in result["sources"])
    assert result["fallback_reason"] == "no previewable content"


def test_rejects_duplicate_selected_images(tmp_path: Path) -> None:
    class DuplicateBackend(FakeBackend):
        def capture_content(self, _url: str, output: Path):
            first = _png(output / "same-a.png", (20, 20, 20))
            second = output / "same-b.png"
            second.write_bytes(first.read_bytes())
            return {
                "directory": _png(output / "directory-source.png", (90, 90, 90)),
                "contents": (first, second),
                "sources": ({"depth": 1}, {"depth": 1}, {"depth": 1}),
            }

    with pytest.raises(ValueError, match="distinct"):
        capture(VALID_URL, tmp_path, backend=DuplicateBackend())


def test_rejects_blank_selected_images(tmp_path: Path) -> None:
    class BlankBackend(FakeBackend):
        def capture_content(self, _url: str, output: Path):
            blank = output / "blank.png"
            Image.new("RGB", (900, 600), "white").save(blank, format="PNG")
            return {
                "directory": _png(output / "directory-source.png", (90, 90, 90)),
                "contents": (blank, _png(output / "usable.png", (20, 80, 160))),
                "sources": ({"depth": 1}, {"depth": 1}, {"depth": 1}),
            }

    with pytest.raises(ValueError, match="blank"):
        capture(VALID_URL, tmp_path, backend=BlankBackend())
