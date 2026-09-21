import { mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import lockfile from 'proper-lockfile';

/** All plugin processes sharing a local store serialize complete read/modify/write transactions. */
export async function withStoreLock<T>(path: string, operation: (assertOwned: () => void) => Promise<T>): Promise<T> {
  await mkdir(dirname(path), { recursive: true });
  let compromised: Error | undefined;
  const release = await lockfile.lock(path, { realpath: false, stale: 30000, update: 5000,
    retries: { retries: 80, factor: 1.1, minTimeout: 20, maxTimeout: 200 }, onCompromised: error => { compromised = error; } });
  try {
    const assertOwned = () => { if (compromised) throw compromised; };
    assertOwned();
    return await operation(assertOwned);
  } finally { await release(); }
}
