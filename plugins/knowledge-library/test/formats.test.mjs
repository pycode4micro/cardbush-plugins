import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { PDFDocument, StandardFonts } from 'pdf-lib';
import JSZip from 'jszip';
import { KnowledgeAPI } from '../src/api.mjs';
import { extract } from '../src/import.mjs';

export async function fixtures() {
  const pdf = await PDFDocument.create(), font = await pdf.embedFont(StandardFonts.Helvetica);
  pdf.addPage().drawText('Aurora travel allowance is 600 yuan per night.', { font, x: 40, y: 700, size: 14 });
  pdf.addPage().drawText('Refunds complete within 3 working days.', { font, x: 40, y: 700, size: 14 });
  const scanned = await PDFDocument.create(); scanned.addPage();
  const zip = new JSZip();
  zip.file('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
  zip.file('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
  zip.file('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>员工差旅制度</w:t></w:r></w:p><w:p><w:r><w:t>上海住宿每晚不超过 600 元。提交报销单需要发票。</w:t></w:r></w:p></w:body></w:document>');
  return { pdf: Buffer.from(await pdf.save()), scanned: Buffer.from(await scanned.save()), docx: await zip.generateAsync({ type: 'nodebuffer' }) };
}

test('real PDF pages, Word text, HTML script removal, CSV and malformed input', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'knowledge-formats-'));
  const api = new KnowledgeAPI({ root }); t.after(() => api.store.close());
  await api.call('knowledge_library', { library_id: 'files', name: '文件解析' });
  const data = await fixtures();
  const files = [
    ['travel.pdf', data.pdf], ['policy.docx', data.docx],
    ['help.html', Buffer.from('<html><head><style>csssecret{}</style><script>scriptsecret()</script></head><body><h1>路由器安装</h1><p>重置键按住 10 秒。</p><iframe src="https://example.invalid/exfil">remote-secret</iframe></body></html>')],
    ['prices.csv', Buffer.from('型号,价格\nAurora,299\n')], ['notes.md', Buffer.from('# 个人笔记\n摄影镜头保持清洁。')],
  ];
  const result = await api.call('knowledge_import', { library_id: 'files', files: files.map(([name,bytes]) => ({ name, data_base64: bytes.toString('base64') })) });
  assert.deepEqual(result.failed, []); assert.equal(result.imported.length, 5);
  const pdfSearch = await api.call('knowledge_search', { query: 'Refunds' });
  assert.equal(pdfSearch.results[0].location.page, 2);
  assert.match(pdfSearch.results[0].text, /3 working days/);
  const wordSearch = await api.call('knowledge_search', { query: '上海住宿' }); assert.match(wordSearch.results[0].text, /600/);
  assert.equal((await api.call('knowledge_search', { query: 'scriptsecret csssecret remote-secret' })).results.length, 0);
  assert.match((await api.call('knowledge_search', { query: '重置键' })).results[0].text, /10 秒/);
  const broken = await api.call('knowledge_import', { library_id: 'files', files: [
    { name: 'scanned.pdf', data_base64: data.scanned.toString('base64') },
    { name: 'broken.pdf', data_base64: Buffer.from('not PDF').toString('base64') },
    { name: 'broken.docx', data_base64: Buffer.from('not ZIP').toString('base64') },
    { name: 'invalid.txt', data_base64: 'not base64!' },
    { name: 'binary.txt', data_base64: Buffer.from([255,0,0]).toString('base64') },
  ] });
  assert.equal(broken.imported.length, 0); assert.equal(broken.failed.length, 5);
  assert.match(broken.failed[0].error, /OCR/);
  assert.equal(api.store.catalog().libraries[0].document_count, 5);
});

test('long documents expose bounded chunks and page forward without losing text', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'knowledge-long-'));
  const api = new KnowledgeAPI({ root }); t.after(() => api.store.close());
  await api.call('knowledge_library', { library_id: 'long', name: '长资料' });
  const text = '背景材料。'.repeat(600) + '\n核心结论：紫杉设备维护间隔为 42 天。\n' + '补充说明。'.repeat(600);
  const result = await api.call('knowledge_import', { library_id: 'long', documents: [{ source_key: 'long-manual', title: '设备手册', text }] });
  const document_id = result.imported[0].id;
  const hit = (await api.call('knowledge_search', { query: '紫杉设备维护间隔' })).results[0]; assert.match(hit.text, /42/);
  assert.ok(hit.text.length <= 652);
  let chunk = 1, visited = 0;
  do { const page = await api.call('knowledge_read', { document_id, chunk, limit: 2 }); assert.ok(page.chunks.length <= 2); visited += page.chunks.length; chunk = page.next_chunk; } while (chunk);
  assert.equal(visited, result.imported[0].chunks);
});

test('parser timeout returns a bounded failure instead of leaving the import pending', async () => {
  await assert.rejects(extract(Buffer.from('ordinary text'), '.txt', 1), /已停止/);
});
