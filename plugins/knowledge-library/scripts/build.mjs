import { build } from 'esbuild';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = await build({ absWorkingDir: root, entryPoints: ['src/server.mjs','src/demo.mjs','src/extract-worker.mjs'], bundle: true, metafile: true,
  platform: 'node', format: 'esm', target: 'node22', outdir: 'runtime', outExtension: { '.js': '.mjs' }, splitting: true,
  banner: { js: "import { createRequire as knowledgeCreateRequire } from 'node:module'; const require = knowledgeCreateRequire(import.meta.url);" },
});
const ui = await build({ absWorkingDir: root, entryPoints: ['ui/library.mjs'], bundle: true, write: false, platform: 'browser', format: 'iife', target: 'chrome120' });
await fs.writeFile(path.join(root, 'runtime/library.html'), (await fs.readFile(path.join(root, 'ui/library.html'), 'utf8')).replace('/* APP_SCRIPT */', () => ui.outputFiles[0].text.replaceAll('</script', '<\\/script')));
const dependencies = [...new Set(Object.keys(output.metafile.inputs).map(p => p.match(/^node_modules\/((?:@[^/]+\/)?[^/]+)/)?.[1]).filter(Boolean))].sort();
let notices = 'Third-party software bundled in Knowledge Library\n';
for (const name of dependencies) {
  const directory = path.join(root, 'node_modules', name), pkg = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'));
  notices += `\n${'='.repeat(60)}\n${name} ${pkg.version}\nLicense: ${pkg.license || 'See upstream'}\n`;
  for (const file of (await fs.readdir(directory)).filter(name => /^(LICENSE|LICENCE|COPYING|NOTICE)([.-]|$)/i.test(name))) {
    if ((await fs.stat(path.join(directory, file))).isFile()) notices += '\n' + await fs.readFile(path.join(directory, file), 'utf8') + '\n';
  }
}
await fs.writeFile(path.join(root, 'THIRD_PARTY_NOTICES.txt'), notices);
// Remove obsolete hashed chunks, never user data or arbitrary paths.
const current = new Set(Object.keys(output.metafile.outputs).map(p => path.basename(p)));
for (const name of await fs.readdir(path.join(root, 'runtime'))) if (/^(?:chunk|pdfjs)-[A-Z0-9]+\.mjs$/.test(name) && !current.has(name)) await fs.unlink(path.join(root, 'runtime', name));
console.log('Built portable MCP, isolated document parser and library panel.');
