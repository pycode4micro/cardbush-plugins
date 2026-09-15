import fs from 'node:fs';
import path from 'node:path';
const MAX_REFERENCE = 50 * 1024 * 1024;
export const OPERATIONS = ['song', 'instrumental', 'lyrics_write', 'lyrics_edit', 'cover_preprocess', 'cover'];
export function checkString(value, name, min = 0, max = Infinity) {
  if (typeof value !== 'string' || [...value].length < min || [...value].length > max) throw new Error(`${name} must be a string of ${min}..${max} characters`);
  return value;
}
function oneOf(value, options, name) {
  if (!options.includes(value)) throw new Error(`${name} must be one of: ${options.join(', ')}`);
  return value;
}
function integer(value, min, max, name) {
  if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${name} must be an integer in ${min}..${max}`);
  return value;
}
export function validateArgs(op, a, cfg) {
  if (!OPERATIONS.includes(op) || !a || typeof a !== 'object' || Array.isArray(a)) throw new Error('Invalid operation or arguments');
  const common = ['output_dir', 'title', 'request_id', 'backend'];
  const music = ['prompt', 'lyrics', 'model', 'lyrics_optimizer', 'stream', 'output_format', 'audio_setting', 'aigc_watermark', 'duration_seconds', 'seed'];
  const refs = ['audio_url', 'audio_base64', 'audio_path', 'cover_feature_id'];
  const allowed = new Set([...common, ...(['song','instrumental'].includes(op) ? music : op === 'cover' ? ['prompt','lyrics','stream','output_format','audio_setting','aigc_watermark',...refs] : op === 'cover_preprocess' ? refs.slice(0,3) : ['prompt','lyrics'])]);
  for (const key of Object.keys(a)) if (!allowed.has(key)) throw new Error(`Unsupported argument for ${op}: ${key}`);
  checkString(a.output_dir, 'output_dir', 1, 4096);
  if (!path.isAbsolute(a.output_dir)) throw new Error('output_dir must be an absolute path');
  if (a.title !== undefined) checkString(a.title, 'title', 0, 200);
  if (a.request_id !== undefined && (typeof a.request_id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(a.request_id))) throw new Error('request_id must contain 1..100 letters, digits, underscores or hyphens');
  const backend = oneOf(a.backend || cfg.backend, ['cloud','local'], 'backend');
  if (backend === 'local') {
    if (!['song','instrumental'].includes(op)) throw new Error('Self-hosted Music 3 supports song/instrumental generation only; lyrics and cover APIs require cloud access');
    for (const key of ['stream','lyrics_optimizer','aigc_watermark']) if (a[key]) throw new Error(`${key} is not supported by the self-hosted adapter`);
    if (a.model && a.model !== 'music-3.0') throw new Error('The local adapter targets Music 3 only');
    if (a.output_format !== undefined || a.audio_setting !== undefined) throw new Error('The self-hosted adapter returns 32 kHz stereo WAV; omit cloud output settings');
    checkString(a.prompt, 'prompt', 1, 12000);
    if (op === 'song') checkString(a.lyrics, 'lyrics', 1, 3500);
    if (a.duration_seconds !== undefined) integer(a.duration_seconds, 1, 300, 'duration_seconds');
    if (a.seed !== undefined) integer(a.seed, 0, 2147483647, 'seed');
    return backend;
  }
  if (a.duration_seconds !== undefined || a.seed !== undefined) throw new Error('The official cloud music API has no duration or seed parameter. Use a prompt hint or explicitly select the local backend');
  for (const key of ['stream','lyrics_optimizer','aigc_watermark']) if (a[key] !== undefined && typeof a[key] !== 'boolean') throw new Error(`${key} must be boolean`);
  if (a.stream && a.output_format === 'url') throw new Error('Streaming requires output_format=hex');
  if (a.stream && a.aigc_watermark) throw new Error('aigc_watermark is supported only with stream=false');
  if (a.output_format !== undefined) oneOf(a.output_format, ['hex','url'], 'output_format');
  if (a.audio_setting !== undefined) {
    if (!a.audio_setting || typeof a.audio_setting !== 'object' || Array.isArray(a.audio_setting)) throw new Error('audio_setting must be an object');
    for (const key of Object.keys(a.audio_setting)) if (!['format','sample_rate','bitrate'].includes(key)) throw new Error(`Unknown audio_setting: ${key}`);
    if (a.audio_setting.format !== undefined) oneOf(a.audio_setting.format, ['mp3','wav','pcm'], 'format');
    if (a.audio_setting.sample_rate !== undefined) oneOf(a.audio_setting.sample_rate, [16000,24000,32000,44100], 'sample_rate');
    if (a.audio_setting.bitrate !== undefined) oneOf(a.audio_setting.bitrate, [32000,64000,128000,256000], 'bitrate');
  }
  if (['song','instrumental'].includes(op)) {
    oneOf(a.model || 'music-3.0', ['music-3.0','music-2.6'], 'model');
    checkString(a.prompt ?? '', 'prompt', op === 'instrumental' || (a.lyrics_optimizer && !a.lyrics) ? 1 : 0, 2000);
    if (op === 'song' && !a.lyrics_optimizer) checkString(a.lyrics, 'lyrics', 1, 3500);
    else if (a.lyrics !== undefined) checkString(a.lyrics, 'lyrics', 0, 3500);
    if (op === 'instrumental' && (a.lyrics || a.lyrics_optimizer)) throw new Error('Instrumental mode must not request sung lyrics');
  } else if (op.startsWith('lyrics_')) {
    checkString(a.prompt ?? '', 'prompt', 0, 2000);
    if (op === 'lyrics_edit') checkString(a.lyrics, 'lyrics', 1, 3500);
    if (op === 'lyrics_write' && a.lyrics !== undefined) throw new Error('Existing lyrics belong in edit_lyrics');
  } else {
    const selected = refs.filter(k => a[k] !== undefined);
    if (selected.length !== 1) throw new Error('Supply exactly one of audio_url, audio_base64, audio_path, or cover_feature_id (cover only)');
    if (a.audio_url !== undefined) {
      const u = new URL(a.audio_url);
      if (u.protocol !== 'https:' || u.username || u.password) throw new Error('audio_url must be a public HTTPS URL without embedded credentials');
    }
    if (a.audio_path !== undefined) {
      if (typeof a.audio_path !== 'string' || !path.isAbsolute(a.audio_path)) throw new Error('audio_path must be absolute');
      const stat = fs.statSync(a.audio_path);
      if (!stat.isFile() || stat.size === 0 || stat.size > MAX_REFERENCE) throw new Error('Reference audio must be a file of 1 byte..50 MiB');
    }
    if (a.audio_base64 !== undefined) {
      checkString(a.audio_base64, 'audio_base64', 4, Math.ceil(MAX_REFERENCE / 3) * 4);
      if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(a.audio_base64) || Buffer.from(a.audio_base64,'base64').length > MAX_REFERENCE) throw new Error('Invalid or oversized audio_base64');
    }
    if (op === 'cover') {
      checkString(a.prompt, 'prompt', 10, 300);
      if (a.cover_feature_id !== undefined) checkString(a.cover_feature_id, 'cover_feature_id', 1, 1000);
      if (a.lyrics !== undefined || a.cover_feature_id !== undefined) checkString(a.lyrics, 'lyrics', 10, 1000);
    }
  }
  return backend;
}
export function buildRequest(op, a, backend) {
  if (backend === 'local') return {endpoint:'/v1/audio/speech', body:{model:'MiniMaxAI/MiniMax-Music3', input:op === 'instrumental' ? '[Instrumental]' : a.lyrics, instructions:(op === 'instrumental' ? 'Instrumental only. No vocals, no singing, no humming. ' : '') + a.prompt, response_format:'wav', seed:a.seed ?? 7, max_new_tokens:Math.round((a.duration_seconds ?? 180) * 25), stream:false}};
  if (op.startsWith('lyrics_')) return {endpoint:'/v1/lyrics_generation',body:{mode:op === 'lyrics_edit' ? 'edit':'write_full_song', ...(a.prompt !== undefined && {prompt:a.prompt}), ...(a.title !== undefined && {title:a.title}), ...(op === 'lyrics_edit' && {lyrics:a.lyrics})}};
  let body = {};
  if (op === 'cover_preprocess' || op === 'cover') {
    body.model = 'music-cover';
    for (const k of ['audio_url','audio_base64','cover_feature_id']) if (a[k] !== undefined) body[k] = a[k];
    if (a.audio_path) body.audio_base64 = fs.readFileSync(a.audio_path).toString('base64');
    if (op === 'cover_preprocess') return {endpoint:'/v1/music_cover_preprocess',body};
  } else {
    body.model = a.model || 'music-3.0';
    body.is_instrumental = op === 'instrumental';
    body.lyrics_optimizer = a.lyrics_optimizer || false;
  }
  for (const k of ['prompt','lyrics','aigc_watermark']) if (a[k] !== undefined) body[k] = a[k];
  body.stream = a.stream || false;
  body.output_format = a.output_format || 'hex';
  body.audio_setting = {format:'wav',sample_rate:44100,bitrate:256000,...a.audio_setting};
  return {endpoint:'/v1/music_generation',body};
}
