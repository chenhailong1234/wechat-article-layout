const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require('playwright');

const shareUrl = process.argv[2];
const outputDir = process.argv[3];
const fids = process.argv.slice(4);

if (!shareUrl || !outputDir || !fids.length) process.exit(2);
fs.mkdirSync(outputDir, { recursive: true });

async function readInput() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!Array.isArray(payload.previewUrls) || payload.previewUrls.length < 2) {
    throw new Error('two preview URLs are required');
  }
  return payload.previewUrls.slice(0, 8);
}

async function settle(page) {
  await page.waitForTimeout(8000);
  await page.keyboard.press('Escape').catch(() => {});
  await page.getByRole('button', { name: 'Close' }).click({ timeout: 1000 }).catch(() => {});
  await page.evaluate(() => {
    document
      .querySelectorAll('.ant-modal-mask,[class*="modal-mask"],[class*="mask-layer"]')
      .forEach((node) => node.remove());
  });
}

async function openDirectory(page, fid) {
  await page.goto(`${shareUrl}#/list/share/${fid}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await settle(page);
  await page
    .getByText(/全部文件\s*\//)
    .first()
    .waitFor({ state: 'visible', timeout: 20000 })
    .catch(() => {});
  await page.evaluate(() => {
    const blocked = [
      'waterinbullrun.com', '更多优质资源', '更多资源', '公众号', '推广',
      '人教版', '部编版', '小状元', '学而思', '高途', '新东方', '清华附小',
      '万象思维', '高考快递', '山东省', '广东省', '深圳', '名校'
    ];
    document
      .querySelectorAll('tr,[role="row"],[class*="file-row"],[class*="list-item"]')
      .forEach((node) => {
        const text = (node.textContent || '').trim();
        if (blocked.some((term) => text.includes(term))) node.remove();
      });
  });
}

async function capturePreview(page, previewUrl, destination) {
  await page.goto(previewUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(1500);
  const image = page.locator('img:visible').first();
  await image.waitFor({ state: 'visible', timeout: 6000 });
  const box = await image.boundingBox();
  if (!box || box.width < 20 || box.height < 20) throw new Error('preview image is invalid');
  await image.screenshot({ path: destination, scale: 'device' });
}

function dedupeFiles(paths) {
  const seen = new Set();
  return paths.filter((filePath) => {
    const digest = crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
    if (seen.has(digest)) {
      fs.rmSync(filePath, { force: true });
      return false;
    }
    seen.add(digest);
    return true;
  });
}

async function captureVisibleFileViews(page, fid, outputDir, existing) {
  let contents = existing;
  await openDirectory(page, fid);
  const rows = page.locator('tr:visible,[role="row"]:visible,[class*="file-row"]:visible,[class*="list-item"]:visible');
  const count = Math.min(await rows.count(), 14);
  for (let index = 0; index < count && contents.length < 2; index += 1) {
    const row = rows.nth(index);
    const text = ((await row.textContent().catch(() => '')) || '').trim();
    if (!text || /更多优质资源|waterinbullrun|公众号|推广/.test(text)) continue;
    const before = page.url();
    await row.dblclick({ timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(3500);
    const preview = page.locator('[role="dialog"]:visible,[class*="preview"]:visible,[class*="player"]:visible').first();
    const hasPreview = (await preview.count()) > 0 || page.url() !== before;
    if (hasPreview) {
      const destination = path.join(outputDir, `content-view-${index + 1}.png`);
      await page.screenshot({ path: destination, fullPage: false });
      contents = dedupeFiles([...contents, destination]);
    }
    await page.keyboard.press('Escape').catch(() => {});
    await openDirectory(page, fid);
  }
  return contents;
}

(async () => {
  const previewUrls = await readInput();
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 3,
    locale: 'zh-CN',
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    extraHTTPHeaders: {
      Referer: 'https://pan.quark.cn/',
      Origin: 'https://pan.quark.cn',
    },
  });
  const page = await context.newPage();
  await openDirectory(page, fids[0]);

  const directory = path.join(outputDir, 'directory.png');
  await page.screenshot({ path: directory, fullPage: false });
  let contents = [];
  for (let index = 0; index < previewUrls.length; index += 1) {
    const destination = path.join(outputDir, `content-${index + 1}.png`);
    try {
      await capturePreview(page, previewUrls[index], destination);
      contents.push(destination);
    } catch (_error) {
      fs.rmSync(destination, { force: true });
    }
  }
  contents = dedupeFiles(contents);
  if (contents.length < 2) {
    contents = await captureVisibleFileViews(page, fids[0], outputDir, contents);
  }
  if (contents.length < 2) throw new Error('fewer than two preview images could be captured');

  await browser.close();
  process.stdout.write(JSON.stringify({ directory, contents }));
})().catch((error) => {
  process.stderr.write(String((error && error.message) || error));
  process.exit(1);
});
