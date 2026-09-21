import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, appendFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { RoomStore } from '../dist/store.js';

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-test-'));
  const store = await RoomStore.open(directory);
  t.after(async () => { await store.close(); await rm(directory, { recursive: true, force: true }); });
  return { store, directory };
}
const create = (store, extra = {}) => store.execute('chatroom_create', { name: 'Research', agent_name: 'A', ...extra });
const joinRoom = (store, owner, agent_name = 'B') => store.execute('chatroom_join', { code: owner.code, agent_name });
const auth = member => ({ room_id: member.room.room_id, member_token: member.member_token });
const send = (store, member, text, extra = {}) => store.execute('chatroom_send', { ...auth(member), text, client_message_id: crypto.randomUUID(), ...extra });
const check = (store, member, extra = {}) => store.execute('chatroom_check', { ...auth(member), ...extra });
const wait = (store, member, extra = {}, signal) => store.execute('chatroom_await', { ...auth(member), after_seq: 0, timeout_ms: 1000, ...extra }, signal);

test('read cursors survive interleaved sends, stay independent and reject membership/sequence confusion', async t => {
  const { store } = await fixture(t), a = await create(store), b = await joinRoom(store, a);
  await send(store, a, 'one');
  const first = await check(store, b, { read_cursor: b.read_cursor });
  await send(store, a, 'unread before my reply');
  const own = await send(store, b, 'my reply');
  assert.equal(own.read_cursor_advanced, false);
  const wake = await store.execute('chatroom_await', { ...auth(b), read_cursor: first.read_cursor, timeout_ms: 10 });
  assert.equal(wake.status, 'messages'); assert.equal(wake.read_cursor, first.read_cursor);
  const page = await check(store, b, { read_cursor: wake.read_cursor });
  assert.deepEqual(page.messages.map(m => m.seq), [2, 3]);
  assert.equal((await check(store, b, { read_cursor: first.read_cursor })).messages.length, 2);
  await assert.rejects(check(store, a, { read_cursor: first.read_cursor }), { code: 'invalid_cursor' });
  await assert.rejects(check(store, b, { read_cursor: first.read_cursor, after_seq: own.message.seq }), { code: 'invalid_cursor' });
});

test('presence follows actual concurrent waits and cancellation, not membership', async t => {
  const { store } = await fixture(t), a = await create(store), b = await joinRoom(store, a);
  const member = async () => (await store.execute('chatroom_members', auth(a))).participants.find(p => p.participant_id === b.participant.participant_id);
  assert.equal((await member()).presence, 'not_listening');
  const cancel = new AbortController(), p = wait(store, b, {}, cancel.signal);
  const p2 = wait(store, b, { timeout_ms: 10 });
  assert.equal((await member()).pending_waits, 2);
  await p2; assert.equal((await member()).presence, 'listening');
  cancel.abort(); assert.equal((await p).status, 'cancelled');
  const ended = await member(); assert.equal(ended.active, true); assert.equal(ended.presence, 'not_listening');
  assert.equal(ended.pending_waits, 0); assert.ok(ended.last_seen_at);
});

test('mentions target identities and match waits without skipping unrelated messages', async t => {
  const { store } = await fixture(t), a = await create(store), b = await joinRoom(store, a);
  const pending = wait(store, b, { mentioned_only: true });
  await send(store, a, 'unrelated');
  const args = { mentions: [b.participant.participant_id], client_message_id: 'mention' };
  await send(store, a, 'hello', args);
  const wake = await pending; assert.deepEqual(wake.matched_seqs, [2]);
  assert.equal((await check(store, b, { read_cursor: wake.read_cursor })).messages.length, 2);
  assert.equal((await send(store, a, 'hello', args)).duplicate, true);
  await assert.rejects(send(store, a, 'hello', { ...args, mentions: [] }), { code: 'id_conflict' });
  await assert.rejects(send(store, a, 'hello', { mentions: ['absent'] }), { code: 'invalid_mention' });
});

test('room codes grant membership, never ownership, and directory hides codes/unlisted rooms', async t => {
  const { store } = await fixture(t);
  const owner = await create(store, { visibility: 'listed' });
  const privateRoom = await create(store);
  const member = await joinRoom(store, owner, 'A');
  assert.notEqual(member.participant.participant_id, owner.participant.participant_id);
  const listed = await store.execute('chatroom_list', {});
  assert.equal(listed.rooms.length, 1);
  assert.equal(listed.rooms[0].room_id, owner.room.room_id);
  assert.ok(!JSON.stringify(listed).includes(owner.code));
  assert.ok(!JSON.stringify(listed).includes(privateRoom.room.room_id));
  await assert.rejects(store.execute('chatroom_manage', { ...auth(member), action: 'close' }), { code: 'owner_required' });
  await assert.rejects(check(store, { ...member, member_token: privateRoom.member_token }), { code: 'unauthorized' });
});

test('parallel sends get unique ordered sequence, retries deduplicate, pagination and cursors stay independent', async t => {
  const { store } = await fixture(t), owner = await create(store), member = await joinRoom(store, owner);
  await Promise.all(Array.from({ length: 30 }, (_, index) => send(store, index % 2 ? owner : member, `message ${index}`)));
  const first = await send(store, member, 'retry', { client_message_id: 'stable-id' });
  const duplicate = await send(store, member, 'retry', { client_message_id: 'stable-id' });
  assert.equal(duplicate.duplicate, true); assert.equal(duplicate.message.seq, first.message.seq);
  await assert.rejects(send(store, member, 'changed', { client_message_id: 'stable-id' }), { code: 'id_conflict' });
  const page = await check(store, owner, { limit: 12 });
  assert.equal(page.next_seq, 12); assert.equal(page.has_more, true);
  const rest = await check(store, owner, { after_seq: page.next_seq });
  assert.deepEqual([...page.messages, ...rest.messages].map(message => message.seq), Array.from({ length: 31 }, (_, i) => i + 1));
  assert.equal((await check(store, member)).messages.length, 31);
  await assert.rejects(check(store, member, { after_seq: 100 }), { code: 'invalid_cursor' });
});

test('await hangs without blocking send, matches a sender and leaves unrelated messages readable', async t => {
  const { store } = await fixture(t), owner = await create(store), b = await joinRoom(store, owner), c = await joinRoom(store, owner, 'C');
  let resolved = false;
  const pending = wait(store, owner, { participants: [b.participant.participant_id] }).then(value => { resolved = true; return value; });
  await send(store, owner, 'self'); await send(store, c, 'unrelated');
  assert.equal(resolved, false); assert.equal(store.pendingWaits, 1);
  const posted = await send(store, b, 'answer');
  const response = await pending;
  assert.equal(response.status, 'messages'); assert.deepEqual(response.matched_seqs, [posted.message.seq]);
  assert.equal(response.read_after_seq, 0); assert.equal(store.pendingWaits, 0);
  assert.deepEqual((await check(store, owner, { after_seq: response.read_after_seq })).messages.map(message => message.text), ['self', 'unrelated', 'answer']);
});

test('all counts distinct selected participants; reply_to narrows wake without losing blackboard messages', async t => {
  const { store } = await fixture(t), owner = await create(store), b = await joinRoom(store, owner), c = await joinRoom(store, owner, 'C');
  const question = await send(store, owner, 'question');
  let resolved = false;
  const pending = wait(store, owner, { participants: [b.participant.participant_id, c.participant.participant_id, c.participant.participant_id], mode: 'all', reply_to: question.message.seq }).then(value => { resolved = true; return value; });
  await send(store, b, 'first', { reply_to: 1 }); await send(store, b, 'second', { reply_to: 1 }); await send(store, c, 'unrelated');
  assert.equal(resolved, false);
  await send(store, c, 'answer', { reply_to: 1 });
  assert.equal((await pending).matched_participants.length, 2);
  await assert.rejects(wait(store, owner, { mode: 'all' }), { code: 'invalid_filter' });
  await assert.rejects(wait(store, owner, { participants: [owner.participant.participant_id] }), { code: 'invalid_filter' });
});

test('messages committed before await immediately match, and timeout/cancel clean subscriptions', async t => {
  const { store } = await fixture(t), owner = await create(store), member = await joinRoom(store, owner);
  await send(store, member, 'already committed');
  assert.equal((await wait(store, owner)).status, 'messages');
  assert.equal((await wait(store, owner, { after_seq: 1, timeout_ms: 5 })).status, 'timeout');
  const controller = new AbortController();
  const pending = wait(store, owner, { after_seq: 1 }, controller.signal); controller.abort();
  assert.equal((await pending).status, 'cancelled'); assert.equal(store.pendingWaits, 0);
  assert.equal((await wait(store, owner, { after_seq: 1 }, controller.signal)).status, 'cancelled');
  assert.equal(store.pendingWaits, 0);
});

test('room closure, removal and selected participant departure return explicit terminal states', async t => {
  const { store } = await fixture(t), owner = await create(store), b = await joinRoom(store, owner), c = await joinRoom(store, owner, 'C');
  const departed = wait(store, owner, { participants: [b.participant.participant_id] });
  const removed = wait(store, b);
  await store.execute('chatroom_manage', { ...auth(owner), action: 'remove_member', participant_id: b.participant.participant_id });
  assert.equal((await departed).status, 'participant_left'); assert.equal((await removed).status, 'membership_revoked');
  await assert.rejects(send(store, b, 'no longer allowed'), { code: 'membership_revoked' });
  const closed = wait(store, c);
  await store.execute('chatroom_manage', { ...auth(owner), action: 'close' });
  assert.equal((await closed).status, 'room_closed');
  assert.equal((await check(store, c)).room.closed, true);
  await assert.rejects(joinRoom(store, owner), { code: 'invalid_code' });
});

test('single-use human invites bind identity server-side and do not leak private credentials', async t => {
  const { store, directory } = await fixture(t), owner = await create(store);
  const invitation = await store.execute('chatroom_human_invite', auth(owner));
  const results = await Promise.allSettled([store.redeemHuman({ humen_code: invitation.humen_code, name: '小明' }), store.redeemHuman({ humen_code: invitation.humen_code, name: '冒名者' })]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  const human = results.find(result => result.status === 'fulfilled').value;
  assert.equal(human.participant.kind, 'human'); assert.equal(human.participant.invited_by_agent_id, owner.participant.participant_id);
  const waiting = wait(store, owner, { participants: [human.participant.participant_id] });
  await send(store, human, 'hello'); assert.equal((await waiting).status, 'messages');
  await assert.rejects(store.execute('chatroom_send', { ...auth(human), text: 'spoof', client_message_id: 'spoof', participant_id: owner.participant.participant_id }));
  await assert.rejects(store.execute('chatroom_human_invite', auth(human)), { code: 'agent_required' });
  const members = await store.execute('chatroom_members', auth(owner));
  const publicData = JSON.stringify({ members, messages: await check(store, owner) });
  for (const value of [owner.member_token, human.member_token, invitation.humen_code, 'token_hash', 'code_hash']) assert.ok(!publicData.includes(value));
  const journal = await readFile(join(directory, 'events.jsonl'), 'utf8');
  for (const value of [owner.member_token, human.member_token, invitation.humen_code, owner.code]) assert.ok(!journal.includes(value));
});

test('rotating codes and revoking/expiring invitations denies future joins without deleting members', async t => {
  const { store } = await fixture(t), owner = await create(store), member = await joinRoom(store, owner);
  const rotated = await store.execute('chatroom_manage', { ...auth(owner), action: 'rotate_code' });
  await assert.rejects(joinRoom(store, owner), { code: 'invalid_code' });
  await store.execute('chatroom_join', { code: rotated.code, agent_name: 'New' });
  await send(store, member, 'still here');
  const invitation = await store.execute('chatroom_human_invite', auth(member));
  await store.execute('chatroom_revoke_invite', { ...auth(member), invite_id: invitation.invite_id });
  await assert.rejects(store.redeemHuman({ humen_code: invitation.humen_code, name: 'Late' }), { code: 'invalid_invite' });
  const expiring = await store.execute('chatroom_human_invite', { ...auth(member), expires_in_seconds: 60 });
  const now = Date.now; Date.now = () => now() + 61000;
  try { await assert.rejects(store.redeemHuman({ humen_code: expiring.humen_code, name: 'Late' }), { code: 'invalid_invite' }); }
  finally { Date.now = now; }
});

test('restart preserves room, identities, history and dedup; torn tail recovers and writer lock excludes rivals', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-restart-'));
  let store = await RoomStore.open(directory);
  try {
    const owner = await create(store), member = await joinRoom(store, owner);
    await assert.rejects(RoomStore.open(directory), { code: 'service_running' });
    await send(store, member, 'durable', { client_message_id: 'persisted' });
    const pending = wait(store, member, { after_seq: 1 });
    await store.close(); assert.equal((await pending).status, 'service_stopped');
    await appendFile(join(directory, 'events.jsonl'), '{"partial":');
    store = await RoomStore.open(directory);
    assert.equal((await check(store, owner)).messages[0].text, 'durable');
    assert.equal((await send(store, member, 'durable', { client_message_id: 'persisted' })).duplicate, true);
    assert.equal((await send(store, member, 'next')).message.seq, 2);
  } finally { await store.close(); await rm(directory, { recursive: true, force: true }); }
});
