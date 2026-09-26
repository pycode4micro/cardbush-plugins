const $ = id => document.getElementById(id);
const element = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
let libraries = [], selected = null, readOnly = false, busy = false, editing = null, serial = 0, nextOffset = null;
const pending = new Map();
const demo = window.parent === window;
function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
function notify(method, params) { parent.postMessage({ jsonrpc: '2.0', method, params }, '*'); }
function rpc(method, params) {
  const id = 'knowledge-' + ++serial;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('连接超时，请刷新核对操作结果。')); }, 120000);
    pending.set(id, { resolve, reject, timer }); parent.postMessage({ jsonrpc: '2.0', id, method, params }, '*');
  });
}
async function tool(name, args = {}) {
  if (demo) {
    const response = await fetch('/api/tool', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Knowledge-Demo': window.__DEMO_TOKEN__ }, body: JSON.stringify({ name, arguments: args }) });
    const result = await response.json(); if (!response.ok) throw new Error(result.error || '请求失败'); return result;
  }
  const result = await rpc('tools/call', { name, arguments: args });
  if (result.isError) throw new Error(result.content?.find(c => c.type === 'text')?.text || '资料库操作失败');
  return result.structuredContent || JSON.parse(result.content.find(c => c.type === 'text').text);
}
function access() {
  $('new-library').classList.toggle('hidden', readOnly);
  $('edit-library').classList.toggle('hidden', readOnly || !selected);
  $('import-controls').classList.toggle('hidden', readOnly || !selected);
  $('access-note').textContent = readOnly ? '当前连接：只读' : '当前连接：可管理';
}
async function work(fn) {
  if (busy) return;
  busy = true; const controls = [...document.querySelectorAll('button,input,textarea')]; controls.forEach(e => { e.disabled = true; });
  try { await fn(); } catch (error) { status(error.message, true); }
  finally { busy = false; controls.forEach(e => { if (e.isConnected) e.disabled = false; }); access(); size(); }
}
function size() { if (!demo) notify('ui/notifications/size-changed', { height: Math.min(850, Math.ceil(document.body.scrollHeight)) }); }
function theme(context) {
  if (context?.theme) document.documentElement.style.colorScheme = context.theme === 'dark' ? 'dark' : 'light';
  for (const [key, value] of Object.entries(context?.styles?.variables || {})) if (/^--[a-z0-9-]+$/.test(key) && typeof value === 'string') document.documentElement.style.setProperty(key, value);
}
function paint() {
  $('libraries').replaceChildren(...libraries.map(library => {
    const button = element('button', undefined, 'library' + (library.id === selected ? ' active' : ''));
    button.append(element('strong', library.name), element('small', `${library.department || '个人'} · ${library.document_count} 份资料`));
    button.onclick = () => work(async () => { selected = library.id; $('results').replaceChildren(); $('query').value = ''; paint(); await documents(); status(''); }); return button;
  }));
  const library = libraries.find(l => l.id === selected);
  $('scope-name').textContent = library?.name || '选择一个资料库';
  $('scope-description').textContent = library?.description || '先导入资料，再用自然语言查找相关内容。';
  $('scope-meta').replaceChildren(...[library?.department, library?.scenario, library ? `${library.document_count} 份资料` : ''].filter(Boolean).map(text => element('span', text)));
  access();
}
async function refresh() {
  const result = await tool('knowledge_catalog'); libraries = result.libraries; readOnly = result.read_only;
  if (!libraries.some(l => l.id === selected)) selected = libraries[0]?.id || null;
  paint(); if (selected) await documents(); status(libraries.length ? '' : '创建一个资料库，再导入已有资料。');
}
function showTab(name) {
  for (const key of ['search','docs']) { $('tab-' + key).classList.toggle('active', key === name); $(key + '-view').classList.toggle('hidden', key !== name); } size();
}
function locationText(location) { return location.page ? `第 ${location.page} 页` : `提取文本 ${location.line_start}–${location.line_end} 行`; }
async function sourceBlock(target, document_id, revision, chunk = 1) {
  const data = await tool('knowledge_read', { document_id, revision, chunk, limit: 3 });
  const block = element('div', undefined, 'source');
  block.append(element('small', `${data.document.title} · v${data.document.revision}${data.document.revision !== data.document.current_revision ? '（历史版本）' : ''}${data.document.archived ? ' · 已归档' : ''}`));
  for (const piece of data.chunks) { block.append(element('small', locationText(piece.location)), element('div', piece.text)); }
  if (data.next_chunk) { const more = element('button', '继续阅读'); more.onclick = () => work(async () => { more.remove(); await sourceBlock(target, document_id, revision, data.next_chunk); }); block.append(more); }
  target.append(block); size();
}
async function search() {
  if (!selected) throw new Error('请先建立或选择资料库。');
  const result = await tool('knowledge_search', { library_ids: [selected], query: $('query').value, limit: 6 });
  $('results').replaceChildren();
  $('search-hint').textContent = `找到 ${result.results.length} 个相关片段${result.expanded_terms.length ? ' · 同义词：' + result.expanded_terms.join('、') : ''}。请核对适用条件和版本。`;
  for (const row of result.results) {
    const card = element('article', undefined, 'result'), top = element('div', undefined, 'result-top'), read = element('button', '查看原文');
    top.append(element('h3', row.title), read);
    card.append(top, element('div', `${row.library_name} · ${locationText(row.location)} · v${row.revision}`, 'result-meta'), element('p', row.text, 'snippet'));
    read.onclick = () => work(async () => { const existing = card.querySelector('.source'); if (existing) { card.querySelectorAll('.source').forEach(e => e.remove()); read.textContent = '查看原文'; } else { await sourceBlock(card, row.document_id, row.revision, row.chunk); read.textContent = '收起原文'; } });
    $('results').append(card);
  }
  if (!result.results.length) { const empty = element('div', undefined, 'empty'); empty.append(element('strong', '没有找到相关原文'), element('span', '换用资料中的关键词，或补充资料／领域同义词。')); $('results').append(empty); }
  status(''); size();
}
async function documents(offset = 0) {
  if (!selected) return;
  const result = await tool('knowledge_catalog', { library_id: selected, include_archived: $('include-archived').checked, offset, limit: 30 });
  if (!offset) $('documents').replaceChildren();
  for (const doc of result.documents) {
    const row = element('div', undefined, 'doc'), title = element('div', undefined, 'doc-title'), actions = element('div', undefined, 'doc-actions');
    title.append(element('strong', doc.title), element('small', `v${doc.revision}${doc.archived ? ' · 已归档' : ''} · ${new Date(doc.updated_at).toLocaleString()}\n${doc.source_key}`));
    const read = element('button', '原文'); read.onclick = () => work(async () => { const old = row.nextElementSibling; if (old?.classList.contains('source')) old.remove(); else { const container = element('div'); await sourceBlock(container, doc.id, doc.revision); row.after(...container.childNodes); } }); actions.append(read);
    if (!readOnly) { const archive = element('button', doc.archived ? '恢复' : '归档'); archive.onclick = () => work(async () => { await tool('knowledge_document', { document_id: doc.id, expected_revision: doc.revision, action: doc.archived ? 'restore' : 'archive' }); await refresh(); status(doc.archived ? '已恢复，可以重新检索。' : '已归档，已从当前检索移除。'); }); actions.append(archive); }
    row.append(title, actions); $('documents').append(row);
  }
  nextOffset = result.next_offset; $('load-more').classList.toggle('hidden', nextOffset === null);
  const library = result.libraries.find(l => l.id === selected); $('doc-count').textContent = `${library.document_count} 份有效资料 · ${library.archived_count} 份归档`;
  if (!result.documents.length && !offset) $('documents').append(element('div', '还没有资料，从下方导入即可。', 'empty'));
}
function edit(library) {
  editing = library || null; $('library-form').classList.remove('hidden'); $('form-title').textContent = library ? '资料库设置' : '新建资料库';
  for (const [key, value] of Object.entries({ name: library?.name, id: library?.id, department: library?.department, scenario: library?.scenario, description: library?.description, aliases: library?.aliases.map(group => group.join(', ')).join('\n') })) $('lib-' + key).value = value || '';
  $('lib-id').readOnly = !!library; $('lib-name').focus(); size();
}
function importReport(result) {
  return `已处理 ${result.imported.length} 份：新增 ${result.imported.filter(d => d.status === 'created').length}，更新 ${result.imported.filter(d => d.status === 'updated').length}，未变化 ${result.imported.filter(d => d.status === 'unchanged').length}。` +
    (result.failed.length ? '\n失败：' + result.failed.map(d => `${d.source}：${d.error}`).join('\n') : '') +
    (result.skipped.length ? `\n跳过 ${result.skipped.length} 个不支持的文件或链接。` : '') +
    result.imported.flatMap(d => d.warnings || []).map(w => '\n' + w).join('');
}
$('tab-search').onclick = () => showTab('search'); $('tab-docs').onclick = () => showTab('docs');
$('refresh').onclick = () => work(refresh); $('new-library').onclick = () => edit(null); $('edit-library').onclick = () => edit(libraries.find(l => l.id === selected));
$('cancel-library').onclick = () => { $('library-form').classList.add('hidden'); size(); };
$('search-form').onsubmit = event => { event.preventDefault(); void work(search); };
$('include-archived').onchange = () => work(() => documents()); $('load-more').onclick = () => work(() => documents(nextOffset));
$('library-form').onsubmit = event => { event.preventDefault(); void work(async () => {
  const args = { library_id: $('lib-id').value.trim(), name: $('lib-name').value, department: $('lib-department').value, scenario: $('lib-scenario').value, description: $('lib-description').value,
    aliases: $('lib-aliases').value.split('\n').map(row => row.split(/[,，]/).map(t => t.trim()).filter(Boolean)).filter(row => row.length), ...(editing ? { expected_revision: editing.revision } : {}) };
  await tool('knowledge_library', args); selected = args.library_id; $('library-form').classList.add('hidden'); await refresh(); status('资料库已保存。');
}); };
$('text-form').onsubmit = event => { event.preventDefault(); void work(async () => {
  if (!selected) throw new Error('请先选择资料库。');
  const result = await tool('knowledge_import', { library_id: selected, documents: [{ source_key: 'note:' + crypto.randomUUID(), title: $('text-title').value, text: $('text-body').value }] });
  if (!result.failed.length) $('text-form').reset(); await refresh(); status(importReport(result), result.failed.length > 0);
}); };
$('path-form').onsubmit = event => { event.preventDefault(); void work(async () => {
  if (!selected) throw new Error('请先选择资料库。');
  const result = await tool('knowledge_import', { library_id: selected, paths: [$('import-path').value] }); await refresh(); status(importReport(result), result.failed.length > 0);
}); };
$('upload').onclick = () => work(async () => {
  if (!selected || !$('import-file').files.length) throw new Error('请先选择资料库和文件。');
  const combined = { imported: [], failed: [], skipped: [] };
  for (const file of $('import-file').files) {
    if (file.size > 16 * 1024 * 1024) { combined.failed.push({ source: file.name, error: '超过 16 MB' }); continue; }
    status('正在导入：' + file.name);
    const base64 = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); });
    const result = await tool('knowledge_import', { library_id: selected, files: [{ name: file.name, data_base64: base64 }] });
    for (const key of ['imported','failed','skipped']) combined[key].push(...result[key]);
  }
  if (!combined.failed.length) $('import-file').value = ''; await refresh(); status(importReport(combined), combined.failed.length > 0);
});
addEventListener('message', event => {
  if (event.source !== parent || event.data?.jsonrpc !== '2.0') return;
  const message = event.data, call = pending.get(message.id);
  if (call) { pending.delete(message.id); clearTimeout(call.timer); message.error ? call.reject(new Error(message.error.message)) : call.resolve(message.result); }
  else if (message.method === 'ui/notifications/host-context-changed') theme(message.params);
  else if (message.method === 'ui/notifications/tool-result' && message.params?.structuredContent?.selected_library) { selected = message.params.structuredContent.selected_library; if (!busy) void work(refresh); }
});
if (demo) { $('demo-label').classList.remove('hidden'); void work(refresh); }
else {
  rpc('ui/initialize', { protocolVersion: '2026-01-26', appInfo: { name: 'knowledge-library', version: '0.1.0' }, appCapabilities: {} })
    .then(result => { theme(result.hostContext); notify('ui/notifications/initialized', {}); void work(refresh); }).catch(error => status(error.message, true));
}
