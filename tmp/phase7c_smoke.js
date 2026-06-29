const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const requests = [];
  const pageErrors = [];
  page.on('request', (req) => {
    const url = req.url();
    if (url.includes('/api/')) requests.push({ method: req.method(), url });
  });
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  await page.goto('http://127.0.0.1:8001/#generate', { waitUntil: 'networkidle' });
  await page.evaluate(() => {
    window.renderDraftResult('8520b0e9eda645edb2637a8a73a33319', '/storage/runs/8520b0e9eda645edb2637a8a73a33319/outputs/8520b0e9eda645edb2637a8a73a33319_draft.png');
  });
  await page.waitForSelector('#visualQaPanel:not([hidden])');
  await page.click('#qaPassedBtn');
  await page.waitForTimeout(800);
  await page.click('#finalizeOutputBtn');
  await page.waitForTimeout(1500);
  const snapshot = await page.evaluate(() => ({
    qaStatusText: document.getElementById('visualQaStatusText')?.textContent || null,
    finalPanelVisible: !document.getElementById('finalOutputPanel')?.hidden,
    finalImageSrc: document.getElementById('finalOutputImage')?.getAttribute('src') || null,
    finalUrlText: document.getElementById('finalOutputUrlLink')?.textContent || null,
    finalUrlHref: document.getElementById('finalOutputUrlLink')?.getAttribute('href') || null,
    runIdText: document.getElementById('runIdText')?.textContent || null,
    outputUrlText: document.getElementById('outputUrlLink')?.textContent || null,
  }));
  console.log(JSON.stringify({ snapshot, requests, pageErrors }, null, 2));
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
