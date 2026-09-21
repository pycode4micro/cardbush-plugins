// Run with a local Electron executable; exercises the real browser and HTTP API.
const { app, BrowserWindow, nativeTheme } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
let service, window, directory;
(async () => {
  directory = await fs.mkdtemp(path.join(os.tmpdir(), 'agentchatroom-web-test-'));
  app.setPath('userData', path.join(directory, 'browser'));
  await app.whenReady();
  const { startService } = await import(pathToFileURL(path.resolve(__dirname, '../dist/service.js')).href);
  service = await startService({ directory: path.join(directory, 'rooms'), webDirectory: path.resolve(__dirname, '../web') });
  const store = service.store;
  const owner = await store.execute('chatroom_create', { name: '未来科技发展方向', description: '聊聊未来 5–20 年的科技变化。\n从 AI、能源到人与技术的关系，一起交换看法。', agent_name: 'CardBush 主代理' });
  const auth = { room_id: owner.room.room_id, member_token: owner.member_token };
  await store.execute('chatroom_send', { ...auth, text: '大家都到了。今天一起聊聊，未来科技会怎样改变我们的生活？', client_message_id: 'welcome' });
  const invite = await store.execute('chatroom_human_invite', auth);
  window = new BrowserWindow({ show: false, width: 1050, height: 780, webPreferences: { contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } });
  const errors = [];
  window.webContents.on('console-message', (_event, details) => { if (details.level === 'error') errors.push(details.message); });
  await window.loadURL(`${service.descriptor.web_url}#humen_code=${invite.humen_code}`);
  const evaluate = code => window.webContents.executeJavaScript(code);
  const until = async expression => {
    for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await new Promise(resolve => setTimeout(resolve, 30)); }
    throw new Error(`UI condition failed: ${expression}; ${JSON.stringify(errors)}`);
  };
  const settleLayout = () => evaluate(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
  const capture = async name => {
    if (!process.env.AGENTCHATROOM_SCREENSHOT_DIR) return;
    // Hidden Chromium windows can throttle transitions; capture the final theme colors.
    const css = await window.webContents.insertCSS('* { transition: none !important; animation: none !important; }');
    await settleLayout();
    await fs.mkdir(process.env.AGENTCHATROOM_SCREENSHOT_DIR, { recursive: true });
    const target = path.join(process.env.AGENTCHATROOM_SCREENSHOT_DIR, name + '.png');
    await fs.writeFile(target, (await window.webContents.capturePage()).toPNG()); console.log(target);
    await window.webContents.removeInsertedCSS(css);
  };
  nativeTheme.themeSource = 'light';
  await capture('chatroom-entry');
  await evaluate(`document.getElementById('name').value='测试用户'; document.getElementById('join-form').requestSubmit()`);
  await until(`document.querySelectorAll('.message').length===1`);
  assert.match(await evaluate(`document.querySelector('.listener-notice').textContent`), /没有 Agent/);
  const waiting = store.execute('chatroom_await', { ...auth, after_seq: 1, mentioned_only: true, timeout_ms: 5000 });
  await evaluate(`refreshMembers()`);
  assert.match(await evaluate(`document.querySelector('.listener-notice').textContent`), /1 位 Agent/);
  await evaluate(`document.querySelector('.message-reply').click(); document.getElementById('message').value='先从未来五年聊起吧，你们觉得哪些变化最值得关注？'; document.getElementById('send-form').requestSubmit()`);
  const wake = await waiting;
  assert.equal(wake.status, 'messages');
  const read = await store.execute('chatroom_check', { ...auth, read_cursor: wake.read_cursor });
  assert.equal(read.messages.length, 1); assert.equal(read.messages[0].kind, 'human');
  assert.equal(read.messages[0].reply_to, 1); assert.deepEqual(read.messages[0].mentions, [owner.participant.participant_id]);
  await until(`document.querySelectorAll('.message').length===2`);
  await evaluate(`document.querySelector('button.reply-note').click()`);
  assert.equal(await evaluate(`document.activeElement.dataset.seq`), '1');
  await evaluate(`refreshMembers()`);
  assert.match(await evaluate(`document.querySelector('.listener-notice').textContent`), /没有 Agent/);
  await window.reload();
  await until(`document.querySelectorAll('.message').length===2`);
  assert.equal(await evaluate(`document.querySelectorAll('.message-own').length`), 1);
  assert.equal(await evaluate(`document.querySelectorAll('.day-separator').length`), 1);
  const practical = await store.execute('chatroom_join', { code: owner.code, agent_name: '务实派' });
  const future = await store.execute('chatroom_join', { code: owner.code, agent_name: '前瞻者' });
  await store.execute('chatroom_send', { room_id: auth.room_id, member_token: practical.member_token, text: '我会先看 AI 与能源。比起更远的想象，算力成本、可靠电力，以及真正能融入日常工作的工具，会更早带来变化。', client_message_id: 'practical' });
  await store.execute('chatroom_send', { room_id: auth.room_id, member_token: future.member_token, text: '我想补充一个角度：未来不只是工具变聪明，也会改变我们协作的方式。\n\n人提出方向，不同 Agent 交换观点，再一起把想法变成行动。就像现在这场对话。', client_message_id: 'future' });
  await store.execute('chatroom_send', { room_id: auth.room_id, member_token: future.member_token, text: '你更期待哪一种变化？', client_message_id: 'future-next' });
  await until(`document.querySelectorAll('.message').length===5`);
  assert.equal(await evaluate(`document.querySelectorAll('.is-continuation').length`), 1);
  assert.equal(await evaluate(`document.querySelector('.has-next .message-reply').getBoundingClientRect().height > 0`), true, 'every grouped message remains replyable');
  window.setContentSize(1280, 820);
  await until(`matchMedia('(min-width: 1100px)').matches && !document.getElementById('people-panel').hidden`);
  await settleLayout();
  await evaluate(`document.getElementById('messages').scrollTop=0`);
  await capture('chatroom-desktop-light');
  nativeTheme.themeSource = 'dark';
  await until(`matchMedia('(prefers-color-scheme: dark)').matches`);
  await capture('chatroom-desktop-dark');
  window.setContentSize(480, 760);
  await until(`document.getElementById('people-panel').hidden`);
  await evaluate(`document.getElementById('messages').scrollTop=0`);
  await capture('chatroom-mobile-dark');
  await evaluate(`document.getElementById('people-toggle').click()`);
  assert.equal(await evaluate(`document.getElementById('people-backdrop').hidden`), false);
  assert.equal(await evaluate(`document.querySelector('.conversation').inert`), true);
  await capture('chatroom-mobile-details');
  await evaluate(`document.getElementById('people-close').click()`);
  assert.equal(await evaluate(`document.querySelector('.conversation').inert`), false);
  assert.equal(await evaluate(`document.activeElement.id`), 'people-toggle');
  for (const width of [480, 375, 320]) {
    window.setContentSize(width, 720); await settleLayout();
    assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`), true, `${width}px page must not overflow`);
    assert.equal(await evaluate(`document.getElementById('send-button').getBoundingClientRect().bottom<=innerHeight`), true);
  }
  await evaluate(`document.getElementById('message').value='多行消息\\n'.repeat(30); document.getElementById('message').dispatchEvent(new Event('input'))`);
  assert.equal(await evaluate(`document.getElementById('message').offsetHeight<=160 && document.getElementById('message').offsetHeight>32`), true);
  await evaluate(`document.getElementById('message').value=''; document.getElementById('message').dispatchEvent(new Event('input'))`);
  assert.equal(await evaluate(`document.getElementById('message').offsetHeight`), 32);
  // Long untrusted text must wrap without becoming DOM, and arrival must not yank a reader to the end.
  const longText = '<img src=x onerror=alert(1)>\n' + '长消息与很长的地址https://example.test/'.repeat(200);
  await store.execute('chatroom_send', { ...auth, text: longText, client_message_id: 'long' });
  await until(`document.querySelectorAll('.message').length===6`);
  assert.equal(await evaluate(`document.querySelector('.message:last-child img')===null`), true);
  assert.equal(await evaluate(`document.getElementById('messages').scrollWidth<=document.getElementById('messages').clientWidth`), true);
  await evaluate(`document.getElementById('messages').scrollTop=0`); await settleLayout();
  await store.execute('chatroom_send', { ...auth, text: '新的进展', client_message_id: 'after-long' });
  await until(`document.querySelectorAll('.message').length===7`);
  assert.equal(await evaluate(`document.getElementById('messages').scrollTop`), 0);
  assert.equal(await evaluate(`document.getElementById('latest').hidden`), false);
  await evaluate(`document.getElementById('latest').click()`);
  assert.equal(await evaluate(`document.getElementById('latest').hidden`), true);
  assert.deepEqual(errors, []);
  console.log('PASS: real HTTP reply/mention, presence, history, own bubbles, grouping, themes, details drawer, 320–1280px layouts, composer resizing, safe long text and scroll preservation');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  window?.destroy(); await service?.close();
  if (directory && path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep) && path.basename(directory).startsWith('agentchatroom-web-test-'))
    await fs.rm(directory, { recursive: true, force: true, maxRetries: 10 }).catch(() => {});
  app.exit(process.exitCode || 0);
});
