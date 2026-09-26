const segmenter = new Intl.Segmenter('zh', { granularity: 'word' });
const stop = new Set('的 了 是 在 和 与 或 请 帮 我 我们 公司 员工 这个 那个 什么 怎么 如何 是否 可以 需要 多少 多久 有关 关于 查询 搜索 资料 信息 规定 the a an is are of for to in how what when can do does please'.split(' '));
export const normalize = text => String(text).normalize('NFKC').toLowerCase();

export function terms(text) {
  const found = [];
  for (const { segment, isWordLike } of segmenter.segment(normalize(text))) {
    if (!isWordLike || stop.has(segment)) continue;
    if (segment.length > 1 || /\d/.test(segment)) found.push(segment);
  }
  return [...new Set(found)];
}

export function tokens(text) {
  const words = terms(text);
  // ICU words preserve meaningful Chinese terms; overlapping bigrams cover unknown names.
  for (const run of normalize(text).matchAll(/[\p{Script=Han}]{2,}/gu)) {
    const chars = [...run[0]];
    for (let i = 0; i + 1 < chars.length; i++) words.push(chars[i] + chars[i + 1]);
  }
  return [...new Set(words)].filter(word => !stop.has(word)).map(word => 't' + Buffer.from(word).toString('hex'));
}

export function expandQuery(query, groups = []) {
  const normalized = normalize(query);
  const expansions = [...new Set(groups.filter(group => group.some(term => normalized.includes(normalize(term)))).flat())].slice(0, 40);
  const words = terms(query);
  const searchTokens = [...new Set([...tokens(query), ...tokens(expansions.join(' '))])].slice(0, 100);
  return { words, expansions, match: searchTokens.map(token => '"' + token + '"').join(' OR ') };
}

export function chunkSegments(segments, max = 900, overlap = 120) {
  const chunks = [];
  for (const segment of segments) {
    const text = segment.text.replace(/\r\n?/g, '\n').replace(/\0/g, '').trim();
    let start = 0;
    while (start < text.length) {
      let end = Math.min(text.length, start + max);
      if (end < text.length) {
        const boundary = Math.max(text.lastIndexOf('\n', end), text.lastIndexOf('。', end), text.lastIndexOf('. ', end));
        if (boundary > start + max * 0.55) end = boundary + 1;
      }
      const location = segment.page ? { page: segment.page, char_start: start, char_end: end }
        : { line_start: text.slice(0, start).split('\n').length, line_end: text.slice(0, end).split('\n').length, char_start: start, char_end: end };
      chunks.push({ text: text.slice(start, end), location });
      if (end === text.length) break;
      start = Math.max(start + 1, end - overlap);
    }
  }
  if (!chunks.length) throw new Error('没有可检索的文本；扫描 PDF 需要先做 OCR。');
  if (chunks.length > 3000) throw new Error('文档过大：demo 每份最多 3000 个片段，请按章节拆分。');
  return chunks;
}

export function excerpt(text, query, limit = 650) {
  const normalized = normalize(text), positions = terms(query).map(term => normalized.indexOf(term)).filter(pos => pos >= 0);
  const start = Math.max(0, (positions.length ? Math.min(...positions) : 0) - 100);
  return { text: (start ? '…' : '') + text.slice(start, start + limit) + (start + limit < text.length ? '…' : ''), truncated: start > 0 || text.length > limit };
}
