import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { link, mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import { withStoreLock } from './storeLock.js';

export function dataDirectory(env = process.env) {
  const configured = env.LOGIC_MEMORY_DATA_DIR?.trim();
  if (configured && !isAbsolute(configured)) throw new Error('LOGIC_MEMORY_DATA_DIR must be an absolute path.');
  return configured || join(homedir(), '.logic-memory');
}
export async function importLegacy(source: string, directory: string) {
  if (!isAbsolute(source)) throw new Error('The import source must be an absolute path.');
  const original = await readFile(source, 'utf8');
  const records: unknown = JSON.parse(original);
  if (!Array.isArray(records) || records.some(item => !item || typeof item !== 'object' || Array.isArray(item))) throw new Error('Invalid legacy store: expected an array of records.');
  await mkdir(directory, { recursive: true });
  const destination = join(directory, 'logic.json');
  await withStoreLock(destination, async assertOwned => {
    const temporary = `${destination}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, original, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
      assertOwned();
      // Publish the validated snapshot atomically, without overwriting an existing store.
      await link(temporary, destination);
    } finally { await unlink(temporary).catch(() => {}); }
  });
  return { imported: records.length, destination, source_preserved: true };
}
