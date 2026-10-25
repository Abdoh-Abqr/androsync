// Parses every ```mermaid block in docs/**/*.md with the real Mermaid parser.
//
// One-time setup (any folder with room for ~150 small packages, not inside the repo):
//   mkdir -p /tmp/mmcheck && cd /tmp/mmcheck && npm i mermaid@10.9.3 jsdom@22
// Run from the repo root:
//   MERMAID_CHECK_DIR=/tmp/mmcheck node scripts/check_mermaid.mjs
// Exit code 1 if any block fails to parse. Node 18 or newer.
import fs from 'fs';
import path from 'path';
import { createRequire } from 'module';
import { pathToFileURL } from 'url';

const dir = process.env.MERMAID_CHECK_DIR;
if (!dir) {
  console.error('Set MERMAID_CHECK_DIR to the folder where mermaid and jsdom were installed (see the header).');
  process.exit(2);
}
const { JSDOM } = createRequire(path.join(dir, 'x.js'))('jsdom');
const dom = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const mermaid = (await import(pathToFileURL(path.join(dir, 'node_modules/mermaid/dist/mermaid.core.mjs')))).default;
mermaid.initialize({ startOnLoad: false });

const files = [];
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith('.md')) files.push(p);
  }
})('docs');

let blocks = 0, bad = 0;
for (const f of files) {
  const text = fs.readFileSync(f, 'utf8');
  for (const m of text.matchAll(/```mermaid\n([\s\S]*?)```/g)) {
    blocks++;
    const line = text.slice(0, m.index).split('\n').length;
    try {
      await mermaid.parse(m[1]);
    } catch (e) {
      bad++;
      console.log(`FAIL ${f}:${line}\n  ${String(e.message || e).split('\n').slice(0, 3).join('\n  ')}`);
    }
  }
}
console.log(bad ? `${bad} of ${blocks} blocks failed` : `all ${blocks} blocks parse`);
process.exit(bad ? 1 : 0);
