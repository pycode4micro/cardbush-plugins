import { runPresentationCli } from '../../../dist/presentation.mjs';

try {
  await runPresentationCli(process.argv.slice(2));
} catch (error) {
  process.stderr.write((error.message || String(error)) + '\n');
  process.exitCode = 1;
}
