// 使用模拟响应验证网页，不向任何真实 VPS 发起测速。
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.LIBRESPEED_BROWSER_CHANNEL});
  try {
    const html = fs.readFileSync(path.join(__dirname, '../new/project/vps.html'), 'utf8');
    const output = process.env.LIBRESPEED_SCREENSHOTS;
    if (output) fs.mkdirSync(output, {recursive: true});
    for (const [name, viewport] of [['desktop', {width: 1440, height: 1000}], ['mobile', {width: 390, height: 844}]]) {
      const context = await browser.newContext({viewport});
      const page = await context.newPage();
      const errors = [];
      let requestCount = 0;
      let malicious = false;
      page.on('pageerror', error => errors.push(String(error)));
      page.on('dialog', dialog => dialog.accept());
      await context.route('**/*', async route => {
        if (route.request().url().includes('/vps-agent/pair.php')) {
          requestCount++;
          const payload = route.request().postDataJSON();
          assert.equal(payload.downloadMegabytes, 64);
          const result = {status: 'ok', download: {status: 'ok', mbps: 80, seconds: 6.7},
            upload: {status: 'ok', mbps: 60, seconds: 4.5},
            latency: {status: 'ok', avgMs: 10, jitterMs: 0.2, lossPct: 0}};
          const reverse = malicious ? {status: 'error', error: '<img src=x onerror="window.injected=true">'} : result;
          return route.fulfill({json: {status: malicious ? 'error' : 'ok', forward: {result}, reverse: {result: reverse}}});
        }
        return route.fulfill({contentType: 'text/html; charset=utf-8', body: html});
      });
      await page.goto('http://127.0.0.1:9876/vps.html');
      await page.locator('#target').fill('http://198.51.100.20:8080');
      await page.locator('#token').fill('browser-test-token');
      await page.locator('#start').click();
      await page.waitForFunction(() => document.getElementById('status').textContent === '已完成');
      assert.equal(requestCount, 1);
      assert.equal(await page.locator('#fixed').getAttribute('readonly'), '');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const clipped = await page.locator('button, label, .metric').evaluateAll(items => items.filter(item => item.scrollWidth > item.clientWidth + 1).length);
      assert.equal(clipped, 0);
      const download = page.waitForEvent('download');
      await page.locator('#download-report').click();
      assert.match((await download).suggestedFilename(), /^librespeed-report-.*\.json$/);
      await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true}));
      await page.locator('#copy').click();
      assert.equal(await page.locator('#status').textContent(), '已复制');
      if (output) await page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
      malicious = true;
      await page.locator('#start').click();
      await page.waitForFunction(() => document.getElementById('status').textContent === '失败');
      assert.equal(await page.locator('#reverse img').count(), 0);
      assert.equal(await page.evaluate(() => window.injected || false), false);
      assert.ok((await page.locator('#reverse').textContent()).includes('<img'));
      assert.deepEqual(errors, []);
      console.log(name + ': 表单、流量确认、指标、复制、下载、布局和错误转义通过');
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
