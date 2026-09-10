# WeChat Article Layout Toolkit

一套用于微信公众号资料文章的可复用工具，包含：

- 固定版式的公众号 HTML 生成与校验
- 30–60 字搜索友好标题的提取、候选生成、评分和自动选择
- 夸克分享目录或实际内容截图；内容不可预览时回退为两张内层目录图
- 从内容截图中选择一张作为文章封面
- 单账号及多账号配置校验
- 通过 Limyai 接口创建公众号草稿

仓库只保存通用程序和测试，不包含 API Key、`.env`、公众号历史文章、网盘链接、截图、发布结果或浏览器登录状态。

## 环境要求

- Python 3.12+
- Node.js 18+
- 可选：本机 Chrome；不设置 `CHROME_PATH` 时使用 Playwright 自带的 Chromium

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Set-Location quarkmover
npm install
Set-Location ..
```

安装 Playwright 浏览器：

```powershell
npx playwright install chromium
```

复制环境变量模板并填写自己的密钥：

```powershell
Copy-Item .env.example .env
```

`.env` 已被 Git 忽略，请勿把真实密钥提交到仓库。

## 核心用法

### 生成公众号文章

```python
from pathlib import Path
from windows_capabilities.wechat_article import WechatArticleInput, build_wechat_article

article = build_wechat_article(
    WechatArticleInput(
        title="五年级数学进阶练习，计算图形重点题型专项训练与课后巩固资料",
        description="包含计算、图形与应用题等重点题型，适合日常练习和阶段巩固。",
        new_link="https://pan.quark.cn/s/replace_me",
        keyword="五年级数学",
        directory_screenshot=Path("screenshots/directory.png"),
        content_screenshots=(Path("screenshots/content-1.png"), Path("screenshots/content-2.png")),
        cover_image=Path("screenshots/content-1.png"),
    ),
    output_dir=Path("output"),
)

print(article.html_path)
```

输入字段以 `WechatArticleInput` 的当前定义为准。生成器会验证链接、图片、标题长度及固定版式，避免静默发布不符合规则的内容。

### 标题自动选择

```python
from windows_capabilities.wechat_title_strategy import extract_title_facts, select_best_title

facts = extract_title_facts("五年级数学进阶练习，计算与图形重点题型")
decision = select_best_title(facts)
print(decision.selected.title, decision.selected.score)
```

### 复用夸克资料截图 Skill

仓库内置 `capture-quark-resource-screenshots` Skill，显示名称为“夸克资料截图（无风险词控制版）”。它会优先生成一张内层目录图和两张真实内容图；资源无法预览时，改为生成两张不同的内层目录图，不使用分享根目录截图。

在完整仓库中完成上述 Python、Node.js 和 Playwright 安装后，可直接运行：

```powershell
python skills/capture-quark-resource-screenshots/scripts/capture.py `
  --url "https://pan.quark.cn/s/replace_me" `
  --output "output/quark-screenshots"
```

也可以把 `skills/capture-quark-resource-screenshots` 安装到 Codex Skills 目录，然后这样调用：

```text
$capture-quark-resource-screenshots 请为这个夸克分享生成公众号素材截图：https://pan.quark.cn/s/replace_me
```

这个版本不执行风险词、品牌名、机构名或地区名过滤，也不声称已做 OCR 合规审核。它只负责截图与结果清单，不负责转存、下载、排版或发布。

### 创建草稿

`scripts/wechat_api.py` 从环境变量 `WECHAT_API_KEY` 或项目根目录 `.env` 读取密钥。发布前请先查看帮助：

```powershell
python scripts/wechat_api.py --help
```

发布会把 API Key 和文章数据发送至 Limyai 服务。请仅在理解并接受其数据处理方式后使用。

## 测试

```powershell
pytest -q
```

## 安全说明

- 仓库默认忽略所有 `.env`、生成文章、截图和发布结果。
- 不要提交浏览器用户目录、Cookie、公众号凭据或真实网盘链接。
- 公开仓库只提交通用代码与测试，不提交密钥、Cookie、用户数据或真实业务素材，并定期轮换第三方 API Key。
- 上传前可执行 `git diff --cached` 和密钥扫描，确认暂存内容符合预期。
