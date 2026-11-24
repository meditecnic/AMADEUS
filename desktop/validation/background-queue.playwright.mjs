import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// Run against Vite with AMADEUS_DEV_MOCK=1. An external Playwright install can
// be selected with AMADEUS_PLAYWRIGHT_MODULE, the absolute path to its entry file.
const { chromium } = await import(process.env.AMADEUS_PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.AMADEUS_PLAYWRIGHT_MODULE).href
  : 'playwright');
const artifacts = process.env.AMADEUS_BROWSER_ARTIFACTS;
const browser = await chromium.launch({ executablePath: process.env.AMADEUS_BROWSER_EXE });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
const fullReply = '接続は安定しているわ。まず、観測したことを一つずつ記録しましょう。結論を急がずに、次の結果を待つの。';

try {
  await page.addInitScript(() => {
    localStorage.setItem('amadeus_pc_worldline', 'steins_gate');
    localStorage.setItem('amadeus_pc_history_collapsed', 'false');
  });
  await page.goto(process.env.AMADEUS_MOCK_URL || 'http://localhost:1420/?scenario=streaming');
  await page.waitForFunction(() => document.querySelector('#salieri-user')?.value === 'Salieri');
  await page.locator('.decrypt-btn').click();
  const composer = page.getByRole('textbox', { name: '输入消息' });
  await page.locator('.sl-root').waitFor({ state: 'detached' });
  const row = id => page.locator(`[data-conversation-id="${id}"]`);
  const select = async id => {
    await row(id).locator('.conversation-select').click();
    await row(id).and(page.locator('.conversation-item.active')).waitFor();
  };
  const send = async text => {
    await composer.fill(text);
    await composer.press('Enter');
  };

  await page.getByRole('button', { name: '新建会话', exact: true }).click();
  const active = page.locator('.conversation-item.active');
  await active.waitFor();
  const other = await active.getAttribute('data-conversation-id');
  assert.ok(other && other !== 'lab-notes');
  await select('lab-notes');
  await send('后台回复验收');
  await row('lab-notes').getByText('回复中', { exact: true }).waitFor();
  await select(other);
  await send('撤回验收');
  const withdraw = page.getByRole('button', { name: '撤回排队', exact: true });
  await withdraw.waitFor();
  assert.equal(await row(other).getByText('排队', { exact: true }).count(), 1);
  assert.equal(await page.getByText('撤回验收', { exact: true }).count(), 1);
  assert.equal(await page.getByText('这个会话还是空的。', { exact: true }).count(), 0);
  await withdraw.click();
  await withdraw.waitFor({ state: 'hidden' });
  assert.equal(await composer.inputValue(), '撤回验收');
  assert.equal(await page.locator('.ws2-lines').getByText('撤回验收', { exact: true }).count(), 0);
  await select('lab-notes');
  await page.getByText('她还在回复。', { exact: true }).waitFor();
  await page.getByText(fullReply, { exact: true }).waitFor();
  assert.equal(await page.getByText(fullReply, { exact: true }).count(), 1);
  assert.equal(await page.getByText('后台回复验收', { exact: true }).count(), 1);

  await send('未读回复验收');
  await select(other);
  await row('lab-notes').getByText('NEW', { exact: true }).waitFor();
  await select('lab-notes');
  await row('lab-notes').getByText('NEW', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(await page.getByText('未读回复验收', { exact: true }).count(), 1);
  assert.deepEqual(errors, []);
  if (artifacts) {
    await mkdir(artifacts, { recursive: true });
    await page.screenshot({ path: join(artifacts, 'background-queue.png') });
  }
  process.stdout.write('Background queue journey passed with 0 console errors.\n');
} finally {
  await browser.close();
}
