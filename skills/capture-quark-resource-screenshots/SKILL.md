---
name: capture-quark-resource-screenshots
description: Use when a public Quark Drive share needs clear screenshots of real resource content or two inner-directory fallback images without downloading files.
---

# Capture Quark Resource Screenshots

生成公开夸克分享的资料展示截图。此 Skill 为“无风险词控制版”：不识别或过滤品牌、机构、教材版本、学校、地区等文字，也不执行 OCR 合规检查。

## 必须使用随附脚本

运行：

```powershell
python scripts/capture.py --url "https://pan.quark.cn/s/分享ID" --output "输出目录"
```

路径相对于本 `SKILL.md` 所在目录。需要指定本机 Chrome 时增加 `--chrome-path "绝对路径"`。先运行 `--help` 可查看参数。

如果缺少 `quarkmover`、Pillow、httpx 或 Playwright，说明缺少的依赖并征得用户同意后安装。不要临时改写另一套截图逻辑。

## 结果判断

读取输出目录中的 `result.json`：

- `mode: content`：应有 `directory.png`、`content-1.png`、`content-2.png`。两张内容图是真实资料预览。
- `mode: directory-only`：应有 `directory-1.png`、`directory-2.png`。两张图都必须来自分享根目录以下，不能用分享根目录截图凑数。

脚本会校验 PNG、最低分辨率、空白图和完全重复图。完成后向用户报告模式和文件绝对路径；需要查看结果时显示实际图片。

## 边界

- 只处理无需登录即可访问的公开分享。
- 不下载或转存网盘文件，不绕过访问控制。
- 不生成公众号文章，不读取 `.env` 或 API Key，不发布草稿。
- 不声称图片经过风险词、版权、品牌或 OCR 审查。`risk_word_control: false` 和 `ocr_reviewed: false` 是预期结果。
- 分享失效、没有内层目录或无法得到两张合格回退图时，明确报告失败，不伪造截图。
