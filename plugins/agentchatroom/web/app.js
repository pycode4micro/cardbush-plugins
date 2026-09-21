'use strict';
const $ = id => document.getElementById(id);
let membership, cursor = 0, stopped = true, generation = 0, sending = false, pendingSend;
let connectionAbort, readCursor, replyTo, lastMessage, lastArticle;
// These controls use textContent throughout; participant text is never HTML.
const composeContext = node('div', 'compose-context');
const replyLabel = node('span', 'reply-label', '');
const clearReply = node('button', 'secondary clear-reply', '取消回复'); clearReply.type = 'button'; clearReply.hidden = true;
const recipient = node('select', 'recipient'); recipient.setAttribute('aria-label', '点名成员');
const listenerNotice = node('p', 'listener-notice'); listenerNotice.setAttribute('role', 'status');
composeContext.append(recipient, replyLabel, clearReply);
$('send-form').prepend(composeContext); $('members').after(listenerNotice);
clearReply.addEventListener('click', () => { replyTo = undefined; replyLabel.textContent = ''; clearReply.hidden = true; });
const wideLayout = matchMedia('(min-width: 1100px)');
let detailsOpen = wideLayout.matches;
function showDetails(open, focus = false) {
  const wasAtLatest = atLatest();
  detailsOpen = open;
  $('workspace').classList.toggle('details-open', open);
  $('people-panel').hidden = !open;
  $('people-backdrop').hidden = !open || wideLayout.matches;
  $('people-toggle').setAttribute('aria-expanded', String(open));
  $('people-toggle').setAttribute('aria-label', open ? '隐藏对话详情' : '显示对话详情');
  // The narrow details drawer is modal within the room; the header remains reachable.
  document.querySelector('.conversation').inert = open && !wideLayout.matches;
  if (wasAtLatest) requestAnimationFrame(() => { $('messages').scrollTop = $('messages').scrollHeight; updateLatest(); });
  if (focus) (open ? $('people-close') : $('people-toggle')).focus();
}
$('people-toggle').addEventListener('click', () => showDetails(!detailsOpen, true));
$('people-close').addEventListener('click', () => showDetails(false, true));
$('people-backdrop').addEventListener('click', () => showDetails(false, true));
document.addEventListener('keydown', event => { if (event.key === 'Escape' && detailsOpen) showDetails(false, true); });
wideLayout.addEventListener('change', () => showDetails(wideLayout.matches));
function atLatest() { const list = $('messages'); return list.scrollHeight - list.scrollTop - list.clientHeight < 100; }
function updateLatest() { $('latest').hidden = atLatest(); }
$('messages').addEventListener('scroll', updateLatest, { passive: true });
$('latest').addEventListener('click', () => { $('messages').scrollTop = $('messages').scrollHeight; updateLatest(); });
new ResizeObserver(updateLatest).observe($('messages'));
function resizeComposer() {
  const input = $('message');
  const wasAtLatest = atLatest();
  input.style.height = 'auto';
  input.style.height = `${Math.min(160, input.scrollHeight)}px`;
  if (wasAtLatest) $('messages').scrollTop = $('messages').scrollHeight;
}
$('message').addEventListener('input', resizeComposer);
function connectionStatus(text, state) { $('connection').textContent = text; $('connection').title = text; $('connection').dataset.state = state; }
function avatar(member) {
  const tone = [...member.participant_id].reduce((hash, letter) => (hash * 31 + letter.charCodeAt(0)) >>> 0, 0) % 5;
  const value = node('span', `avatar tone-${tone}`, Array.from(member.name)[0] || '?');
  value.setAttribute('aria-hidden', 'true'); return value;
}
const dayKey = value => new Date(value).toDateString();
function dayLabel(value) {
  const date = new Date(value), today = new Date(), yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  if (dayKey(date) === dayKey(today)) return '今天';
  if (dayKey(date) === dayKey(yesterday)) return '昨天';
  return date.toLocaleDateString('zh-CN', { ...(date.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}), month: 'long', day: 'numeric', weekday: 'long' });
}
let refreshing = false;
setInterval(async () => {
  if (stopped || refreshing || !membership) return;
  refreshing = true;
  try { await refreshMembers(connectionAbort?.signal); } catch { /* receive owns reconnect UI */ }
  finally { refreshing = false; }
}, 10000);
const seen = new Set();
// getRandomValues is available for explicitly configured HTTP LAN origins too.
const messageId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
const label = member => member.kind === 'human' ? '人类' : 'Agent';
const initialCode = new URLSearchParams(location.hash.slice(1)).get('humen_code');
if (initialCode) { $('invite').value = initialCode; history.replaceState(null, '', location.pathname); }
const errors = { invalid_invite: '邀请码无效、已过期或已使用，请向 Agent 获取新邀请。', unauthorized: '登录已失效，请重新获取邀请加入。', membership_revoked: '你已离开聊天室或被房主移出。', room_closed: '聊天室已关闭。', rate_limit: '请求过于频繁，稍后会自动重试。', id_conflict: '这条消息的重试标识已被使用，请修改消息后重试。' };
async function api(action, value, signal) {
  const response = await fetch(`/api/${action}`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value), signal });
  const data = await response.json();
  if (!response.ok) { const error = new Error(errors[data.error] || data.message || '连接失败，请稍后重试。'); error.code = data.error; throw error; }
  return data;
}
const credentials = () => ({ room_id: membership.room.room_id });
function node(tag, className, text) { const value = document.createElement(tag); if (className) value.className = className; if (text !== undefined) value.textContent = text; return value; }
function showRoom() {
  stopped = false; cursor = 0; readCursor = membership.read_cursor; replyTo = undefined; replyLabel.textContent = ''; clearReply.hidden = true;
  pendingSend = undefined; seen.clear(); generation++; connectionAbort?.abort(); connectionAbort = new AbortController();
  $('entry').hidden = true; $('room').hidden = false; $('message').disabled = false; $('send-button').disabled = false;
  document.body.classList.add('room-active'); showDetails(wideLayout.matches);
  $('messages').querySelectorAll('.message, .day-separator').forEach(item => item.remove()); $('empty').hidden = false;
  lastMessage = undefined; lastArticle = undefined;
  $('message').value = ''; resizeComposer(); $('send-error').textContent = '';
  connectionStatus('连接中', 'connecting');
  $('composer-identity').textContent = `${membership.participant.name} · 人类`;
  $('room-name').textContent = membership.room.name; $('room-description').textContent = membership.room.description;
  sessionStorage.setItem('agentchatroom_membership', JSON.stringify(membership));
  void receive(generation, connectionAbort.signal);
}
function append(messages) {
  const list = $('messages'), nearBottom = atLatest();
  for (const message of messages) {
    if (seen.has(message.seq)) continue;
    seen.add(message.seq); $('empty').hidden = true;
    const sameDay = lastMessage && dayKey(lastMessage.timestamp) === dayKey(message.timestamp);
    if (!sameDay) list.append(node('div', 'day-separator', dayLabel(message.timestamp)));
    const own = message.participant_id === membership.participant.participant_id;
    const article = node('article', `message${own ? ' message-own' : ''}`); article.dataset.seq = String(message.seq);
    article.dataset.participantId = message.participant_id;
    article.setAttribute('aria-label', `${message.name}的消息`);
    const body = node('div', 'message-body'), bubble = node('div', 'message-bubble');
    const continuing = sameDay && lastMessage.participant_id === message.participant_id
      && new Date(message.timestamp) - new Date(lastMessage.timestamp) < 180000 && !message.reply_to && !lastMessage.reply_to;
    if (continuing) { article.classList.add('is-continuation'); lastArticle.classList.add('has-next'); }
    if (!own) article.append(avatar(message));
    const head = node('div', 'message-head');
    head.append(node('span', 'message-name', own ? '你' : message.name), node('span', `badge ${message.kind}`, label(message)));
    if (message.invited_by_agent_id) head.title = `由 ${agentName(message.invited_by_agent_id)} 邀请`;
    body.append(head);
    const source = message.reply_to ? [...list.querySelectorAll('.message')].find(item => item.dataset.seq === String(message.reply_to)) : undefined;
    if (message.reply_to) {
      const sourceName = source?.querySelector('.message-name')?.textContent;
      const reference = node('button', 'reply-note secondary', sourceName ? `↩ 回复 ${sourceName}` : `查看原消息 #${message.reply_to}`); reference.type = 'button';
      reference.setAttribute('aria-label', `查看原消息 #${message.reply_to}`);
      reference.title = source?.querySelector('.message-text')?.textContent?.slice(0, 200) || `消息 #${message.reply_to}`;
      reference.addEventListener('click', () => {
        const target = [...list.querySelectorAll('.message')].find(item => item.dataset.seq === String(message.reply_to));
        if (target) { target.tabIndex = -1; target.focus(); target.scrollIntoView({ block: 'center' }); }
      }); bubble.append(reference);
    }
    const otherMentions = (message.mentions || []).filter(id => id !== source?.dataset.participantId);
    if (otherMentions.length) bubble.append(node('div', 'reply-note', `@ ${otherMentions.map(agentName).join('、')}`));
    bubble.append(node('p', 'message-text', message.text)); body.append(bubble);
    const meta = node('div', 'message-meta');
    const time = node('time', '', new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })); time.dateTime = message.timestamp; time.title = new Date(message.timestamp).toLocaleString(); meta.append(time);
    const reply = node('button', 'message-reply', '回复'); reply.type = 'button';
    reply.setAttribute('aria-label', `回复 ${message.name}的消息`);
    reply.addEventListener('click', () => {
      replyTo = message.seq; replyLabel.textContent = `回复 ${message.name} #${message.seq}`;
      clearReply.hidden = false; recipient.value = message.participant_id; $('message').focus();
    }); meta.append(reply); if (own) meta.append(node('span', '', '已发送'));
    body.append(meta); article.append(body); list.append(article);
    lastMessage = message; lastArticle = article;
  }
  if (nearBottom) list.scrollTop = list.scrollHeight;
  updateLatest();
}
let participants = [];
function agentName(id) { return participants.find(member => member.participant_id === id)?.name || `Agent ${id.slice(0, 8)}`; }
async function refreshMembers(signal) {
  const data = await api('members', credentials(), signal); if (signal?.aborted || stopped) return;
  participants = data.participants;
  $('room-name').textContent = data.room.name; $('room-name').title = data.room.name;
  $('room-description').textContent = data.room.description || '这场对话还没有介绍。';
  const count = participants.filter(member => member.active).length;
  $('member-count').textContent = String(count); $('room-summary').textContent = `${count} 位参与者 · 群组对话`;
  const fragment = document.createDocumentFragment();
  for (const member of participants) {
    const item = node('div', `person${member.active ? '' : ' departed'}`);
    item.append(avatar(member));
    const detail = node('div'); detail.append(node('div', 'person-name', `${member.name}${member.participant_id === membership.participant.participant_id ? '（你）' : ''}`));
    const presence = !member.active ? '已离开' : member.presence === 'listening' ? '正在等待消息' : '当前未监听';
    detail.append(node('div', 'person-detail', `${label(member)}${member.invited_by_agent_id ? ` · 由 ${agentName(member.invited_by_agent_id)} 邀请` : ''}`));
    const status = node('div', `person-presence${member.presence === 'listening' ? ' listening' : ''}`, presence);
    if (member.last_seen_at) status.title = `最近活动 ${new Date(member.last_seen_at).toLocaleString()}`;
    detail.append(status);
    item.append(detail); fragment.append(item);
  }
  $('members').replaceChildren(fragment);
  const selected = recipient.value;
  recipient.replaceChildren(new Option('对所有人发言', ''), ...participants.filter(member => member.active && member.participant_id !== membership.participant.participant_id)
    .map(member => new Option(`${member.name} · ${member.participant_id.slice(0, 6)}`, member.participant_id)));
  if ([...recipient.options].some(option => option.value === selected)) recipient.value = selected;
  const listening = participants.filter(member => member.kind === 'agent' && member.presence === 'listening').length;
  listenerNotice.textContent = listening ? `${listening} 位 Agent 正在等待消息；是否回复由各自决定。` : '消息会保存，目前没有 Agent 正在等待回复。点名不会唤醒已结束的会话。';
}
function endRoom(text) { stopped = true; connectionStatus(text, 'offline'); $('message').disabled = true; $('send-button').disabled = true; $('send-error').textContent = text; }
async function receive(run, signal) {
  let retry = 1000;
  while (!stopped && run === generation) {
    try {
      await refreshMembers(signal);
      if (signal.aborted || run !== generation) return;
      let page;
      do {
        page = await api('check', { ...credentials(), ...(readCursor ? { read_cursor: readCursor } : { after_seq: cursor }) }, signal);
        if (signal.aborted || run !== generation) return;
        append(page.messages); cursor = page.next_seq; readCursor = page.read_cursor;
      } while (page.has_more && !stopped && run === generation);
      if (page.room.closed) return endRoom('聊天室已关闭');
      connectionStatus('已连接', 'connected'); retry = 1000;
      const wake = await api('await', { ...credentials(), ...(readCursor ? { read_cursor: readCursor } : { after_seq: cursor }), timeout_ms: 25000 }, signal);
      if (signal.aborted || run !== generation) return;
      if (wake.status === 'membership_revoked') return endRoom('你已离开聊天室');
      // Closure is read through check on the next iteration, including any final messages.
    } catch (error) {
      if (signal.aborted || run !== generation) return;
      if (['unauthorized', 'membership_revoked'].includes(error.code)) return endRoom(error.message);
      connectionStatus('正在重新连接…', 'offline');
      await new Promise(resolve => setTimeout(resolve, retry)); retry = Math.min(15000, retry * 2);
    }
  }
}
$('join-form').addEventListener('submit', async event => {
  event.preventDefault(); $('join-button').disabled = true; $('join-error').textContent = '';
  try {
    membership = await api('join', { humen_code: $('invite').value.trim(), name: $('name').value.trim() });
    $('invite').value = ''; showRoom();
  } catch (error) { $('join-error').textContent = error.message; }
  finally { $('join-button').disabled = false; }
});
$('send-form').addEventListener('submit', async event => {
  event.preventDefault(); if (sending || stopped) return;
  const text = $('message').value; if (!text.trim()) return;
  const mentions = recipient.value ? [recipient.value] : [];
  if (!pendingSend || pendingSend.text !== text || pendingSend.reply_to !== replyTo || JSON.stringify(pendingSend.mentions) !== JSON.stringify(mentions))
    pendingSend = { text, client_message_id: messageId(), ...(replyTo ? { reply_to: replyTo } : {}), mentions };
  sending = true; $('send-button').disabled = true; $('send-error').textContent = '';
  try {
    await api('send', { ...credentials(), ...pendingSend });
    // Wake our own outstanding read without inserting a later message ahead of older unread ones.
    if ($('message').value === text) { $('message').value = ''; resizeComposer(); clearReply.click(); }
    pendingSend = undefined; generation++; connectionAbort?.abort(); connectionAbort = new AbortController();
    void receive(generation, connectionAbort.signal);
  } catch (error) { $('send-error').textContent = error.message; }
  finally { sending = false; $('send-button').disabled = stopped; }
});
$('message').addEventListener('keydown', event => { if (!event.isComposing && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); $('send-form').requestSubmit(); } });
$('leave').addEventListener('click', async () => {
  $('leave').disabled = true;
  try {
    await api('leave', credentials()); stopped = true; generation++; connectionAbort?.abort(); sessionStorage.removeItem('agentchatroom_membership');
    $('room').hidden = true; $('entry').hidden = false; document.body.classList.remove('room-active');
    $('name').focus();
  } catch (error) { $('send-error').textContent = error.message; }
  finally { $('leave').disabled = false; }
});
if (!initialCode) {
  try { const saved = JSON.parse(sessionStorage.getItem('agentchatroom_membership')); if (saved?.room?.room_id && saved?.participant?.participant_id) { membership = saved; showRoom(); } } catch { sessionStorage.removeItem('agentchatroom_membership'); }
}
