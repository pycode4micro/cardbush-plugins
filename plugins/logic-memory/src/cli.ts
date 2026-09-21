#!/usr/bin/env node
import { join } from 'node:path';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { LogicMemoryStore } from './logicMemory.js';
import { createLogicServer } from './mcp.js';
import { dataDirectory, importLegacy } from './storage.js';

try {
  const directory = dataDirectory(), command = process.argv[2] ?? 'stdio';
  if (command === 'stdio' && process.argv.length <= 3) {
    const handle = serveStdio(() => createLogicServer(new LogicMemoryStore(join(directory, 'logic.json'))));
    process.stdin.once('end', () => { void handle.close(); });
  } else if (command === 'import' && process.argv[3] === '--from' && process.argv.length === 5) {
    console.log(JSON.stringify(await importLegacy(process.argv[4]!, directory), null, 2));
  } else throw new Error('Usage: node runtime/cli.mjs [stdio | import --from <absolute legacy logic.json>]');
} catch (error) { console.error(error instanceof Error ? error.message : String(error)); process.exitCode = 1; }
