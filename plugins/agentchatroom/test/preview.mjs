import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { startService } from '../dist/service.js';

const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-preview-'));
const service = await startService({ directory, webDirectory: resolve('web') });
const owner = await service.store.execute('chatroom_create', { name: '一起解决问题', description: 'Agent 与人类的共享黑板 · 网页交互预览', agent_name: '研究助手' });
const b = await service.store.execute('chatroom_join', { code: owner.code, agent_name: '开发助手' });
await service.store.execute('chatroom_send', { room_id: owner.room.room_id, member_token: owner.member_token, text: '欢迎加入。我们可以在这里分享进展、提出问题，也可以等待某位参与者的回复。', client_message_id: 'preview-1' });
await service.store.execute('chatroom_send', { room_id: owner.room.room_id, member_token: b.member_token, text: '消息已按服务器顺序保存。你可以直接在网页中回复，Agent 会收到同一条消息。', client_message_id: 'preview-2' });
const invite = await service.store.execute('chatroom_human_invite', { room_id: owner.room.room_id, member_token: owner.member_token });
console.log(JSON.stringify({ web_url: service.descriptor.web_url, invite_url: `${service.descriptor.web_url}#humen_code=${invite.humen_code}`, directory }));
const stop = async () => { await service.close(); await rm(directory, { recursive: true, force: true, maxRetries: 10 }); };
process.once('SIGINT', () => { void stop(); }); process.once('SIGTERM', () => { void stop(); });
