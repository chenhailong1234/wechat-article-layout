# Capture Quark Resource Screenshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and distribute a reusable Codex Skill named `capture-quark-resource-screenshots` whose display edition is “无风险词控制版”, producing two real content screenshots or two inner-directory fallback screenshots from a public Quark share.

**Architecture:** A small Python CLI validates inputs, calls the existing tested Quark metadata and capture library, normalizes output names, validates image quality, and writes a machine-readable result. The Skill instructions route agents to this deterministic CLI. No risk-word filtering, OCR compliance checking, downloading, transfer, article formatting, credentials, or publishing is included.

**Tech Stack:** Python 3.12, Pillow, httpx, Playwright 1.55, pytest, Codex Skill metadata.

---

## File map

- `skills/capture-quark-resource-screenshots/SKILL.md`: invocation rules, workflow, boundaries, result interpretation.
- `skills/capture-quark-resource-screenshots/agents/openai.yaml`: display metadata.
- `skills/capture-quark-resource-screenshots/scripts/capture.py`: stable CLI and JSON contract.
- `skills/capture-quark-resource-screenshots/scripts/playwright_capture_content.js`: content preview capture helper.
- `skills/capture-quark-resource-screenshots/scripts/playwright_capture_directory.js`: inner-directory capture helper.
- `skills/capture-quark-resource-screenshots/tests/test_capture.py`: CLI behavior tests.
- `README.md`: installation and invocation link.

### Task 1: Define the CLI contract with failing tests

**Files:**
- Create: `skills/capture-quark-resource-screenshots/tests/test_capture.py`
- Create: `skills/capture-quark-resource-screenshots/scripts/__init__.py`

- [ ] **Step 1: Write failing tests for URL validation and result shape**

```python
def test_rejects_non_quark_url(tmp_path):
    with pytest.raises(ValueError, match="public Quark share URL"):
        capture("https://example.com/file", tmp_path)


def test_content_mode_writes_stable_result(tmp_path, fake_backend):
    result = capture(VALID_URL, tmp_path, backend=fake_backend.content())
    assert result["mode"] == "content"
    assert [Path(p).name for p in result["screenshots"]] == [
        "directory.png", "content-1.png", "content-2.png"
    ]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q skills/capture-quark-resource-screenshots/tests/test_capture.py`

Expected: collection fails because `scripts.capture` does not exist.

- [ ] **Step 3: Commit the failing contract**

```powershell
git add skills/capture-quark-resource-screenshots/tests skills/capture-quark-resource-screenshots/scripts/__init__.py
git commit -m "test: define Quark screenshot skill contract"
```

### Task 2: Implement capture and fallback behavior

**Files:**
- Create: `skills/capture-quark-resource-screenshots/scripts/capture.py`
- Create: `skills/capture-quark-resource-screenshots/scripts/playwright_capture_content.js`
- Create: `skills/capture-quark-resource-screenshots/scripts/playwright_capture_directory.js`
- Modify: `skills/capture-quark-resource-screenshots/tests/test_capture.py`

- [ ] **Step 1: Add failing tests for directory fallback, duplicate rejection, and no-risk-control behavior**

```python
def test_falls_back_to_two_inner_directory_images(tmp_path, fake_backend):
    result = capture(VALID_URL, tmp_path, backend=fake_backend.no_preview())
    assert result["mode"] == "directory-only"
    assert [Path(p).name for p in result["screenshots"]] == [
        "directory-1.png", "directory-2.png"
    ]
    assert all(item["depth"] >= 1 for item in result["sources"])


def test_does_not_filter_names_or_claim_ocr_review(tmp_path, fake_backend):
    result = capture(VALID_URL, tmp_path, backend=fake_backend.named("品牌版资料.pdf"))
    assert result["risk_word_control"] is False
    assert result["ocr_reviewed"] is False
```

- [ ] **Step 2: Run the new tests and verify RED**

Expected: assertions fail because fallback and explicit edition metadata are absent.

- [ ] **Step 3: Implement the minimal public API and CLI**

```python
def capture(url: str, output: Path, *, backend: CaptureBackend | None = None) -> dict[str, object]:
    share_url = validate_share_url(url)
    output = validate_output_directory(output)
    active_backend = backend or QuarkCaptureBackend()
    try:
        bundle = active_backend.capture_content(share_url, output)
        result = content_result(bundle)
    except NoUsableScreenshot as error:
        bundle = active_backend.capture_inner_directories(share_url, output)
        result = directory_result(bundle, reason=str(error))
    validate_selected_images(result)
    result.update({"risk_word_control": False, "ocr_reviewed": False})
    write_result_json(output / "result.json", result)
    return result
```

CLI arguments: `--url`, `--output`, optional `--chrome-path`. Print only the result JSON to stdout; diagnostics go to stderr; failures return non-zero without exposing cookies or tokens.

- [ ] **Step 4: Run focused and existing screenshot tests**

Run:

```powershell
pytest -q skills/capture-quark-resource-screenshots/tests/test_capture.py quarkmover/tests/test_screenshots.py
node --check skills/capture-quark-resource-screenshots/scripts/playwright_capture_content.js
node --check skills/capture-quark-resource-screenshots/scripts/playwright_capture_directory.js
```

Expected: all pass.

- [ ] **Step 5: Commit implementation**

```powershell
git add skills/capture-quark-resource-screenshots/scripts skills/capture-quark-resource-screenshots/tests
git commit -m "feat: add reusable Quark screenshot CLI"
```

### Task 3: Write and behavior-test the Skill instructions

**Files:**
- Create: `skills/capture-quark-resource-screenshots/SKILL.md`
- Create: `skills/capture-quark-resource-screenshots/agents/openai.yaml`
- Modify: `skills/capture-quark-resource-screenshots/tests/test_capture.py`

- [ ] **Step 1: Record baseline failure scenarios before writing `SKILL.md`**

Scenarios must check that an uninstructed agent does not reliably preserve all three invariants: no root-directory screenshot, exactly two real content screenshots when possible, and two inner-directory images on fallback. Record the observed omissions in the test notes.

- [ ] **Step 2: Write the minimal Skill**

Frontmatter:

```yaml
---
name: capture-quark-resource-screenshots
description: Use when a public Quark Drive share needs clear screenshots of real resource content or inner-directory fallback images without downloading files.
---
```

The instructions must explicitly state that “无风险词控制版” has no risk-word filtering or OCR review, must call `scripts/capture.py`, must not use the share root as a fallback image, and must report the paths and selected mode.

- [ ] **Step 3: Run the same application scenarios with the Skill**

Expected: the agent invokes the supplied script, interprets `content` versus `directory-only`, and does not claim risk-word review.

- [ ] **Step 4: Validate metadata**

Run:

```powershell
python C:\Users\dell\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\capture-quark-resource-screenshots
```

Expected: `Skill is valid!`

- [ ] **Step 5: Commit Skill metadata**

```powershell
git add skills/capture-quark-resource-screenshots/SKILL.md skills/capture-quark-resource-screenshots/agents/openai.yaml
git commit -m "feat: add Quark resource screenshot skill"
```

### Task 4: Verify, install, and publish

**Files:**
- Modify: `README.md`
- Install copy: `C:\Users\dell\.codex\skills\capture-quark-resource-screenshots\`

- [ ] **Step 1: Document GitHub installation and one command example**

Add a concise README section linking the Skill folder and showing `$capture-quark-resource-screenshots` with a public Quark link and output directory.

- [ ] **Step 2: Run all repository tests and secret/path scans**

```powershell
pytest -q
rg -n "C:\\Users\\dell|WECHAT_API_KEY|Cookie|pan\.quark\.cn/s/[A-Za-z0-9]{8,}" skills/capture-quark-resource-screenshots
git diff --check
```

Expected: tests pass; scans find no credential, real share ID, or user-specific absolute path in the Skill.

- [ ] **Step 3: Run one authorized live acceptance capture**

Use a valid public share supplied for testing. Confirm image dimensions, nonblank variance, distinct hashes, depth metadata, and visually inspect the images. Do not download source files.

- [ ] **Step 4: Install the verified Skill locally**

Copy only the verified Skill directory to `C:\Users\dell\.codex\skills\capture-quark-resource-screenshots` and re-run `quick_validate.py` against the installed copy.

- [ ] **Step 5: Commit and push the public repository**

```powershell
git add README.md skills/capture-quark-resource-screenshots
git commit -m "docs: publish Quark screenshot skill"
git push origin main
```

- [ ] **Step 6: Verify remote visibility and commit**

Run `gh repo view chenhailong1234/wechat-article-layout --json visibility,url,defaultBranchRef` and compare `git rev-parse HEAD` with `git rev-parse origin/main`.
