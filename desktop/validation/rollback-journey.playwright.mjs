import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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

/** Read confirmation and busy-error copy directly from MessageMenu.tsx (single source of truth). */
function loadMessageMenuCopy() {
  const source = readFileSync(new URL('../src/workstation/MessageMenu.tsx', import.meta.url), 'utf8');
  const pick = (pattern, label) => {
    const match = source.match(pattern);
    assert.ok(match, `Failed to extract ${label} from desktop/src/workstation/MessageMenu.tsx`);
    return match.slice(1);
  };
  const [busyQueued, busyReplying] = pick(
    /if \(code === 'conversation_busy'\) return hasQueuedTurn\s*\?\s*'([^']+)'\s*:\s*'([^']+)'/,
    'conversation_busy messages',
  );
  const [editTitle, rollbackTitle] = pick(/title=\{isEdit \? '([^']+)' : '([^']+)'\}/, 'dialog titles');
  const [editConfirm, rollbackConfirm] = pick(/confirmLabel=\{isEdit \? '([^']+)' : '([^']+)'\}/, 'confirm labels');
  const [stampLabel] = pick(/<Stamp[^>]*>([^<]+)<\/Stamp>/, 'stamp label');
  const [editAction] = pick(/<p>([^<]*\{target\.deletedTurns\}[^<]*)<\/p>\s*<\/>/, 'edit action copy');
  const [rollbackAction] = pick(/\) : \(\s*<p>([^<]*\{target\.deletedTurns\}[^<]*)<\/p>/, 'rollback action copy');
  const [forgetNotice] = pick(/\)\}\s*<p>([^<]*\{target\.deletedTurns\}[^<]*)<\/p>/, 'forget notice');
  const [hintNotice] = pick(/<p className="ws2-dialog-hint">([^<]+)<\/p>/, 'dialog hint');
  const renderTurns = (template, turns) => template.replaceAll('{target.deletedTurns}', String(turns));
  return {
    busyQueued, busyReplying, editTitle, rollbackTitle, editConfirm, rollbackConfirm,
    stampLabel, editAction, rollbackAction, forgetNotice, hintNotice, renderTurns,
  };
}

const copy = loadMessageMenuCopy();
const errors = [];
let allowBusy409 = false;
let voiceStopFrames = 0;

page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
page.on('console', message => {
  if (message.type() !== 'error') return;
  const text = message.text();
  const url = message.location()?.url || '';
  if (allowBusy409 && text.includes('409') && url.includes('/truncate')) return;
  errors.push(`console.error: ${text} (${url})`);
});
page.on('websocket', ws => {
  ws.on('framesent', ({ payload }) => {
    if (typeof payload !== 'string') return;
    try {
      if (JSON.parse(payload).type === 'voice.stop') voiceStopFrames += 1;
    } catch {
      // Ignore non-JSON frames.
    }
  });
});

async function assertConfirmCopy(dialog, mode, deletedTurns) {
  await dialog.waitFor();
  const text = await dialog.innerText();
  const title = mode === 'edit' ? copy.editTitle : copy.rollbackTitle;
  const confirmLabel = mode === 'edit' ? copy.editConfirm : copy.rollbackConfirm;
  const actionLine = copy.renderTurns(mode === 'edit' ? copy.editAction : copy.rollbackAction, deletedTurns);
  const forgetLine = copy.renderTurns(copy.forgetNotice, deletedTurns);

  // 1) Component parity (extracted from MessageMenu.tsx without duplicating strings here).
  assert.ok(text.includes(title), `Missing dialog title "${title}" in: ${text}`);
  assert.ok(text.includes(copy.stampLabel), `Missing stamp "${copy.stampLabel}" in: ${text}`);
  assert.ok(text.includes(`${deletedTurns} 轮`), `Missing turn count "${deletedTurns} 轮" in: ${text}`);
  assert.ok(text.includes(actionLine), `Missing action line "${actionLine}" in: ${text}`);
  assert.ok(text.includes(forgetLine), `Missing forget line "${forgetLine}" in: ${text}`);
  assert.ok(text.includes(copy.hintNotice), `Missing hint line "${copy.hintNotice}" in: ${text}`);
  assert.equal(await dialog.getByRole('button', { name: confirmLabel, exact: true }).count(), 1);

  // 2) Spec §2 invariants (turn count, forget deleted turns, keep pinned/confirmed/edited, irreversible).
  assert.match(text, new RegExp(`删除[^\\n]*${deletedTurns}\\s*轮`));
  assert.match(text, new RegExp(`忘掉[^\\n]*${deletedTurns}\\s*轮`));
  assert.match(text, /置顶、手动确认或手动修改过的记忆会保留/);
  assert.match(text, /无法撤销/);
}

try {
  await page.addInitScript(() => {
    localStorage.setItem('amadeus_pc_worldline', 'steins_gate');
    localStorage.setItem('amadeus_pc_history_collapsed', 'false');
  });
  await page.goto(process.env.AMADEUS_MOCK_URL || 'http://localhost:1420/?scenario=rollback');
  await page.waitForFunction(() => document.querySelector('#salieri-user')?.value === 'Salieri');
  await page.locator('.decrypt-btn').click();
  await page.locator('.sl-root').waitFor({ state: 'detached' });

  const composer = page.getByRole('textbox', { name: '输入消息' });
  const lines = page.locator('.ws2-lines article.ws2-line');
  const dialog = page.getByRole('alertdialog');
  const row = id => page.locator(`[data-conversation-id="${id}"]`);
  const select = async id => {
    await row(id).locator('.conversation-select').click();
    await row(id).and(page.locator('.conversation-item.active')).waitFor();
  };
  const send = async text => {
    await composer.fill(text);
    await composer.press('Enter');
  };
  const openMenuAction = async (messageSnippet, actionName) => {
    const article = lines.filter({ hasText: messageSnippet }).first();
    await article.waitFor();
    await article.hover();
    await article.locator('.ws2-msg-more').click();
    const item = page.getByRole('menuitem', { name: actionName, exact: true });
    await item.waitFor();
    await item.click();
  };

  // --- Stage 1: Busy rejection (queued turn -> 409 conversation_busy, zero messages deleted) ---
  await select('rollback-busy');
  await lines.filter({ hasText: '忙态第二轮：忙时不能被删。' }).waitFor();
  assert.equal(await lines.count(), 4);
  await send('占位开启回复以触发排队忙态');
  await row('rollback-busy').getByText('回复中', { exact: true }).waitFor();
  await select('rollback-main');
  await lines.filter({ hasText: '第三轮：这条会被回滚删掉。' }).waitFor();
  assert.equal(await lines.count(), 6);
  await send('触发排队忙态');
  const withdrawBtn = page.getByRole('button', { name: '撤回排队', exact: true });
  await withdrawBtn.waitFor();
  assert.equal(await row('rollback-main').getByText('排队', { exact: true }).count(), 1);
  await openMenuAction('第一轮观测已经记下了。', '回到这里…');
  await dialog.waitFor();
  allowBusy409 = true;
  const [busyQueueRes] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/conversations/rollback-main/truncate') && r.request().method() === 'POST'),
    dialog.getByRole('button', { name: copy.rollbackConfirm, exact: true }).click(),
  ]);
  assert.equal(busyQueueRes.status(), 409);
  assert.equal((await busyQueueRes.json())?.detail?.code, 'conversation_busy');
  await dialog.getByRole('alert').getByText(copy.busyQueued, { exact: true }).waitFor();
  assert.equal(await lines.filter({ hasText: '第三轮：这条会被回滚删掉。' }).count(), 1);
  assert.equal(await lines.count(), 6);
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  allowBusy409 = false;
  await withdrawBtn.click();
  await withdrawBtn.waitFor({ state: 'hidden' });
  await composer.fill('');
  await row('rollback-busy').getByText('回复中', { exact: true }).waitFor({ state: 'hidden' });

  // --- Stage 1b: Busy rejection when returning to a background-running conversation ---
  await select('rollback-busy');
  await send('后台续跑忙态验证');
  await row('rollback-busy').getByText('回复中', { exact: true }).waitFor();
  await select('rollback-main');
  await select('rollback-busy');
  await page.getByText('她还在回复。', { exact: true }).waitFor();
  await openMenuAction('最初的前提原样保留。', '回到这里…');
  await dialog.waitFor();
  allowBusy409 = true;
  const bgBusyStartedAt = Date.now();
  const [busyBgRes] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/conversations/rollback-busy/truncate') && r.request().method() === 'POST'),
    dialog.getByRole('button', { name: copy.rollbackConfirm, exact: true }).click(),
  ]);
  assert.ok(Date.now() - bgBusyStartedAt < 2000, 'Expected immediate busy rejection without waiting for background turn');
  assert.equal(busyBgRes.status(), 409);
  assert.equal((await busyBgRes.json())?.detail?.code, 'conversation_busy');
  await dialog.getByRole('alert').getByText(copy.busyReplying, { exact: true }).waitFor();
  assert.equal(await row('rollback-busy').getByText('回复中', { exact: true }).count(), 1);
  assert.equal(await lines.filter({ hasText: '忙态第二轮：忙时不能被删。' }).count(), 1);
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  allowBusy409 = false;
  await row('rollback-busy').getByText('回复中', { exact: true }).waitFor({ state: 'hidden' });
  await select('rollback-main');

  // --- Stage 2: Rollback ("回到这里") + rollback confirmation copy ---
  await openMenuAction('先把第二轮假设暂存下来。', '回到这里…');
  await assertConfirmCopy(dialog, 'rollback', 1);
  const [rollbackRes] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/conversations/rollback-main/truncate') && r.request().method() === 'POST'),
    dialog.getByRole('button', { name: copy.rollbackConfirm, exact: true }).click(),
  ]);
  assert.equal(rollbackRes.status(), 200);
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await lines.filter({ hasText: '第三轮：这条会被回滚删掉。' }).count(), 0);
  assert.equal(await lines.filter({ hasText: '第三轮结果属于待回滚内容。' }).count(), 0);
  assert.equal(await lines.filter({ hasText: '第一轮：确认基准记录。' }).count(), 1);
  assert.equal(await lines.filter({ hasText: '第二轮：这条稍后会被改写。' }).count(), 1);
  assert.equal(await lines.count(), 4);

  // --- Stage 3: Edit-and-resend ("改这句重发") success + edit confirmation copy ---
  await openMenuAction('第二轮：这条稍后会被改写。', '改这句重发…');
  await assertConfirmCopy(dialog, 'edit', 1);
  const editArea = dialog.getByTestId('truncate-edit-textarea');
  assert.equal(await editArea.inputValue(), '第二轮：这条稍后会被改写。');
  const editedSuccess = '改写后的第二轮问题';
  await editArea.fill(editedSuccess);
  const hydratedAfterEdit = page.waitForResponse(
    r => r.url().includes('/api/conversations/rollback-main/messages') && r.request().method() === 'GET' && r.status() === 200,
  );
  await dialog.getByRole('button', { name: copy.editConfirm, exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await lines.filter({ hasText: '第二轮：这条稍后会被改写。' }).count(), 0);
  await lines.filter({ hasText: editedSuccess }).waitFor();
  await page.getByText('别急着下结论，等下一次结果。', { exact: true }).waitFor();
  await hydratedAfterEdit;
  assert.equal(await composer.inputValue(), '');
  assert.equal(await lines.count(), 4);

  // --- Stage 4: Edit-and-resend send failure keeps edited text in composer ---
  await openMenuAction(editedSuccess, '改这句重发…');
  await dialog.waitFor();
  const editedFailure = '发送失败保留稿 [MOCK_SEND_FAIL]';
  await dialog.getByTestId('truncate-edit-textarea').fill(editedFailure);
  await dialog.getByRole('button', { name: copy.editConfirm, exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  const toast = page.getByTestId('message-action-toast');
  await toast.waitFor();
  assert.match(await toast.innerText(), /改好的话已留在输入框里/);
  assert.equal(await composer.inputValue(), editedFailure);
  assert.equal(await lines.filter({ hasText: '第一轮：确认基准记录。' }).count(), 1);
  await composer.fill('');

  // --- Stage 5: Voice stop before truncate ("语音停播") ---
  await select('rollback-busy');
  await lines.filter({ hasText: '忙态第二轮：忙时不能被删。' }).waitFor();
  const replayBar = page.getByTestId('replay-status-bar');
  const plate = page.locator('.ws2-plate');
  await openMenuAction('回复进行中可不能删掉。', '重播');
  await replayBar.getByText('正在重播', { exact: true }).waitFor();
  await page.locator('.ws2-plate[data-presence="speaking"]').waitFor();

  const stopBeforeCount = voiceStopFrames;
  let stoppedBeforeDelete = false;
  await page.route('**/api/conversations/rollback-busy/truncate*', async route => {
    await replayBar.waitFor({ state: 'hidden', timeout: 2000 });
    await page.locator('.ws2-plate[data-presence="listening"]').waitFor({ timeout: 2000 });
    assert.ok(voiceStopFrames > stopBeforeCount, 'Expected voice.stop WS frame before truncate request');
    assert.equal(
      await lines.filter({ hasText: '忙态第二轮：忙时不能被删。' }).count(),
      1,
      'Expected truncated message to still exist before truncate response completes',
    );
    stoppedBeforeDelete = true;
    await route.continue();
  }, { times: 1 });

  await openMenuAction('最初的前提原样保留。', '回到这里…');
  await dialog.waitFor();
  assert.equal(await replayBar.isVisible(), true);
  assert.equal(await plate.getAttribute('data-presence'), 'speaking');
  await dialog.getByRole('button', { name: copy.rollbackConfirm, exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(stoppedBeforeDelete, true, 'Truncate route interceptor must verify stop-before-delete');
  assert.equal(await lines.filter({ hasText: '忙态第二轮：忙时不能被删。' }).count(), 0);
  assert.equal(await lines.filter({ hasText: '忙态第一轮：保持不动。' }).count(), 1);
  assert.equal(await lines.count(), 2);

  assert.deepEqual(errors, []);
  if (artifacts) {
    await mkdir(artifacts, { recursive: true });
    await page.screenshot({ path: join(artifacts, 'rollback-journey.png') });
  }
  process.stdout.write('Rollback journey passed with 0 unexpected console errors.\n');
} finally {
  await browser.close();
}
