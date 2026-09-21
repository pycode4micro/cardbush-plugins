import { build } from 'rolldown';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
await mkdir(new URL('./runtime/', import.meta.url), { recursive: true });
await build({ input: 'src/cli.ts', platform: 'node',
  output: { file: 'runtime/cli.mjs', format: 'esm', codeSplitting: false, minify: false } });
// The distributed bundle works without node_modules; ship its dependency licenses too.
const visited = new Set(), notices = ['# Bundled dependency notices\n'];
async function notice(name) {
  if (visited.has(name)) return;
  visited.add(name);
  const directory = join('node_modules', name), metadata = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
  notices.push(`## ${name} ${metadata.version} (${metadata.license})\n`);
  const licenses = (await readdir(directory)).filter(file => /^licen[cs]e(?:\.|$)/i.test(file));
  if (!licenses.length) throw new Error(`Missing bundled license for ${name}`);
  for (const file of licenses) notices.push(await readFile(join(directory, file), 'utf8'));
  for (const dependency of Object.keys(metadata.dependencies ?? {})) await notice(dependency);
}
const manifest = JSON.parse(await readFile('package.json', 'utf8'));
for (const name of Object.keys(manifest.dependencies)) await notice(name);
await writeFile('THIRD_PARTY_NOTICES.md', notices.join('\n\n'));
