const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const shareUrl = process.argv[2];
const outputDir = process.argv[3];
const fids = process.argv.slice(4, 6);
if (!shareUrl || !outputDir || !fids.length) process.exit(2);
fs.mkdirSync(outputDir, { recursive: true });

async function settle(page) {
  await page.waitForTimeout(10000);
  await page.keyboard.press('Escape').catch(() => {});
  await page.evaluate(() => {
    for (const selector of ['[class*="login"]','[class*="Login"]','[class*="qrcode"]','[class*="QrCode"]','[class*="download-client"]','[class*="DownloadClient"]']) {
      document.querySelectorAll(selector).forEach((node) => {
        if (/登录|扫码|客户端|下载App|打开APP/.test((node.textContent || '').trim())) node.style.display = 'none';
      });
    }
    document.querySelectorAll('.ant-modal-mask,[class*="modal-mask"],[class*="ModalMask"],[class*="mask-layer"]').forEach((node) => node.remove());
  });
}

async function openStable(page, url) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle(page);
    const text = await page.locator('body').innerText().catch(() => '');
    if (
      !/网络异常|请稍后重试/.test(text) &&
      /全部文件\s*\/\s*\S+/.test(text)
    ) return;
    await page.waitForTimeout(2500 * (attempt + 1));
  }
  throw new Error(`Unable to render inner directory: ${url}`);
}

(async () => {
  const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY;
  const executablePath = process.env.CHROME_PATH;
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}), ...(proxy ? { proxy: { server: proxy } } : {}), args: ['--disable-blink-features=AutomationControlled'] });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 3,
    locale: 'zh-CN',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();
  const first = path.join(outputDir, 'directory-1.png');
  const second = path.join(outputDir, 'directory-2.png');

  await openStable(page, `${shareUrl}#/list/share/${fids[0]}`);
  await page.screenshot({ path: first, fullPage: false });

  if (fids[1]) {
    await openStable(page, `${shareUrl}#/list/share/${fids[1]}`);
  } else {
    await page.evaluate(() => window.scrollTo(0, Math.min(260, document.body.scrollHeight - window.innerHeight)));
    await page.waitForTimeout(1000);
  }
  await page.screenshot({
    path: second,
    clip: { x: 80, y: 118, width: 1120, height: 520 }
  });
  await browser.close();
  process.stdout.write(JSON.stringify({ paths: [first, second] }));
})().catch(async (error) => { process.stderr.write(String(error && error.message || error)); process.exit(1); });
