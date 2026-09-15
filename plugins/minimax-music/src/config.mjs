import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export const ACCESS_NOTICE = 'As of 2026-09-15: MiniMax stopped new-user paid music/lyrics API access on 2026-08-20. Existing paid users may continue; free music APIs are discontinued. Audio web access and self-hosted Music 3 are separate options.';
export function dataRoot(env = process.env) {
  return path.resolve(env.MINIMAX_MUSIC_DATA_DIR || env.PLUGIN_DATA || path.join(os.homedir(), '.local', 'share', 'minimax-music'));
}
export function configPath(env = process.env) {
  return path.resolve(env.MINIMAX_MUSIC_CONFIG || path.join(os.homedir(), '.config', 'minimax-music', 'config.json'));
}
export function loadConfig(env = process.env) {
  const file = configPath(env);
  const cfg = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')) : {};
  const backend = env.MINIMAX_MUSIC_BACKEND || cfg.backend || 'cloud';
  if (!['cloud', 'local'].includes(backend)) throw new Error('backend must be cloud or local');
  const region = env.MINIMAX_REGION || cfg.region || 'cn';
  if (!['cn', 'global'].includes(region)) throw new Error('region must be cn or global');
  const apiBase = region === 'cn' ? 'https://api.minimax.cn' : 'https://api.minimax.io';
  const localBase = (env.MINIMAX_LOCAL_BASE_URL || cfg.localBaseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '');
  const localUrl = new URL(localBase);
  if (!['http:', 'https:'].includes(localUrl.protocol) || localUrl.username || localUrl.password || localUrl.search || localUrl.hash) throw new Error('Invalid localBaseUrl');
  if (localUrl.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(localUrl.hostname)) throw new Error('Use HTTPS for a remote self-hosted server');
  const keyFile = env.MINIMAX_API_KEY_FILE || cfg.apiKeyFile;
  const key = env.MINIMAX_API_KEY || (keyFile ? fs.readFileSync(path.resolve(path.dirname(file), keyFile), 'utf8').trim() : '');
  const timeoutSeconds = Number(env.MINIMAX_TIMEOUT_SECONDS || cfg.timeoutSeconds || 900);
  if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 3600) throw new Error('timeoutSeconds must be 1..3600');
  return {backend, region, apiBase, localBase, key, localKey: env.MINIMAX_LOCAL_API_KEY || '', timeoutSeconds, dataDir: dataRoot(env), configFile: file};
}
export function requireAccess(cfg, backend) {
  if (backend === 'cloud' && !cfg.key) throw new Error('MINIMAX_API_KEY is not configured. Use the setup command or a private API key file. ' + ACCESS_NOTICE);
}
export function safeError(error, cfg = {}) {
  let message = String(error?.message || error);
  for (const secret of [cfg.key, cfg.localKey]) if (secret) message = message.split(secret).join('[REDACTED]');
  return message.replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]').slice(0, 1500);
}
