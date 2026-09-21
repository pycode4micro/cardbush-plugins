import { build } from 'rolldown';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
await mkdir(join(root, 'runtime'), { recursive: true });
await build({ input: join(root, 'src/cli.ts'), platform: 'node',
  output: { file: join(root, 'runtime/cli.mjs'), format: 'esm', codeSplitting: false, minify: false } });
// The portable runtime includes dependencies, so distribute their licenses with it.
const visited = new Set(), notices = ['# Bundled dependency notices\n'];
async function notice(name) {
  if (visited.has(name)) return;
  visited.add(name);
  const directory = join(root, 'node_modules', name);
  const metadata = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
  notices.push(`## ${name} ${metadata.version} (${metadata.license})\n`);
  const licenses = (await readdir(directory)).filter(file => /^licen[cs]e(?:\.|$)/i.test(file));
  if (!licenses.length) throw new Error(`Missing bundled license for ${name}`);
  for (const file of licenses) notices.push(await readFile(join(directory, file), 'utf8'));
  for (const dependency of Object.keys(metadata.dependencies ?? {})) await notice(dependency);
}
const manifest = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
for (const name of Object.keys(manifest.dependencies)) await notice(name);
await writeFile(join(root, 'THIRD_PARTY_NOTICES.md'), notices.join('\n\n').replace(/[\t ]+$/gm, ''));
console.log(`Ready to import: ${root}`);
