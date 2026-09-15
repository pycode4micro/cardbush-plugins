export class ApiError extends Error {
  constructor(message, {uncertain = false, code} = {}) { super(message); this.uncertain = uncertain; this.code = code; }
}
const MAX_AUDIO = 150 * 1024 * 1024;
export function checkResponse(data) {
  const code = data?.base_resp?.status_code;
  if (code !== undefined && code !== 0) {
    const hints = {1002:'Rate limited. Do not automatically repeat a generation.',1004:'Check API credentials and existing paid music access.',1008:'Insufficient account balance.',2013:'Invalid API parameters.',2049:'Invalid API key.'};
    throw new ApiError(`MiniMax ${code}: ${data.base_resp.status_msg || 'API failure'}. ${hints[code] || ''}`, {code});
  }
}
export function decodeHex(text) {
  if (typeof text !== 'string' || !text.length || text.length % 2 || !/^[\da-f]+$/i.test(text) || text.length > MAX_AUDIO * 2) throw new ApiError('Invalid, empty or oversized hex audio');
  return Buffer.from(text, 'hex');
}
export async function readLimited(response, limit = MAX_AUDIO) {
  const chunks = []; let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > limit) { await response.body.cancel?.().catch(()=>{}); throw new ApiError('Response exceeds size limit'); }
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}
export async function parseSSE(response, onProgress = () => {}) {
  let buffer = '', final = null, hexParts = [], hexLength = 0, completed = false;
  const decoder = new TextDecoder();
  async function event(block) {
    const text = block.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
    if (!text || text === '[DONE]') return;
    let frame; try { frame = JSON.parse(text); } catch { throw new ApiError('Malformed music SSE frame', {uncertain:true}); }
    checkResponse(frame);
    const piece = frame.data?.audio;
    if (piece) {
      decodeHex(piece);
      const prior = frame.data?.status === 2 ? hexParts.join('') : '';
      // Some deployments repeat the whole audio in the terminal frame.
      if (frame.data?.status === 2 && prior && piece.startsWith(prior)) { hexParts = [piece]; hexLength = piece.length; }
      else { hexParts.push(piece); hexLength += piece.length; }
      if (hexLength > MAX_AUDIO * 2) throw new ApiError('Stream audio exceeds size limit');
    }
    if (frame.data?.status === 2) { completed = true; final = frame; }
    await onProgress({audio_bytes:hexLength / 2});
  }
  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, {stream:true});
    let match;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const block = buffer.slice(0,match.index); buffer = buffer.slice(match.index+match[0].length);
      await event(block);
    }
    if (buffer.length > MAX_AUDIO * 2) throw new ApiError('SSE frame exceeds size limit');
  }
  buffer += decoder.decode();
  if (buffer.trim()) await event(buffer);
  if (!completed) throw new ApiError('Stream ended without completed status; generation outcome is unknown. Do not automatically regenerate.',{uncertain:true});
  return {json:final, audio:decodeHex(hexParts.join(''))};
}
export async function callApi(cfg, backend, request, onProgress, fetcher = fetch) {
  const base = backend === 'cloud' ? cfg.apiBase : cfg.localBase;
  const key = backend === 'cloud' ? cfg.key : cfg.localKey;
  const headers = {'Content-Type':'application/json',Accept:request.body.stream ? 'text/event-stream':'application/json'};
  if (key) headers.Authorization = `Bearer ${key}`;
  const signal = AbortSignal.timeout(cfg.timeoutSeconds * 1000);
  let res;
  try { res = await fetcher(base + request.endpoint, {method:'POST',headers,body:JSON.stringify(request.body),redirect:'error',signal}); }
  catch (e) { throw new ApiError(`Request interrupted (${e.name}); completion and billing are unknown. No automatic POST retry.`,{uncertain:true}); }
  if (!res.ok) {
    const bytes = await readLimited(res, 1024 * 1024);
    let info; try { info = JSON.parse(bytes.toString()); } catch { info = null; }
    if (info) checkResponse(info);
    throw new ApiError(`HTTP ${res.status}: MiniMax request failed`, {uncertain:res.status >= 500,code:res.status});
  }
  try {
    if (backend === 'local') {
      const contentType = res.headers.get('content-type') || '';
      if (!/audio\/|octet-stream/i.test(contentType)) throw new ApiError('Expected WAV audio from the Music 3 self-hosted service');
      const audio = await readLimited(res);
      if (audio.subarray(0,4).toString() !== 'RIFF' || audio.subarray(8,12).toString() !== 'WAVE') throw new ApiError('Self-hosted service returned invalid WAV');
      return {audio, json:{backend:'local',format:'wav',sample_rate:32000}};
    }
    if (res.headers.get('content-type')?.includes('text/event-stream')) return await parseSSE(res,onProgress);
    let json; try { json = JSON.parse((await readLimited(res,MAX_AUDIO * 2 + 1048576)).toString('utf8')); } catch (e) { if(e instanceof ApiError) throw e; throw new ApiError('Invalid JSON from MiniMax',{uncertain:true}); }
    checkResponse(json);
    if (request.endpoint === '/v1/music_generation' && json.data?.status !== 2) throw new ApiError('Music response is not marked completed; do not automatically regenerate',{uncertain:true});
    return {json, ...(request.body.output_format === 'hex' && json.data?.audio && {audio:decodeHex(json.data.audio)})};
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(`Response interrupted (${e.name}); generation outcome unknown`,{uncertain:true});
  }
}
export async function downloadAudio(url, timeoutSeconds = 120, fetcher = fetch) {
  const u = new URL(url);
  if (u.protocol !== 'https:' || u.username || u.password) throw new ApiError('Generated audio URL must use HTTPS without embedded credentials');
  // Deliberately no Authorization: signed result URLs never receive an API key.
  const res = await fetcher(url,{signal:AbortSignal.timeout(timeoutSeconds * 1000),redirect:'error'});
  if (!res.ok) throw new ApiError(`Audio download HTTP ${res.status}; retry download, not generation`);
  if (/text\/html|application\/json/i.test(res.headers.get('content-type') || '')) throw new ApiError('Download returned text instead of audio');
  const bytes = await readLimited(res);
  if (!bytes.length) throw new ApiError('Empty audio download');
  return bytes;
}
