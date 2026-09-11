import { build } from 'esbuild'
import { mkdir, writeFile } from 'node:fs/promises'
await mkdir('dist/fixture', { recursive: true })
await build({ entryPoints: ['tests/fixture-entry.js'], bundle: true, format: 'esm', outfile: 'dist/fixture/app.js', alias: { '@hermes/plugin-sdk': new URL('./fixture-sdk.js', import.meta.url).pathname } })
await writeFile('dist/fixture/index.html', `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Virtuoso synthetic test host</title><style>
:root { color-scheme: dark; --ui-text-primary:#e8e9ee; --ui-text-secondary:#b6b9c6; --ui-text-tertiary:#a5a9b8; --ui-stroke-secondary:#454b60; --ui-accent:#a5b4fc; }
body { margin:0; background:#171a24; color:var(--ui-text-primary); font:15px/1.6 system-ui; } * { box-sizing:border-box } button,input,textarea,select { font:inherit; color:inherit; background:#252a3a; border:1px solid var(--ui-stroke-secondary); border-radius:6px; padding:9px 14px } button { cursor:pointer } button:disabled { opacity:.5; cursor:default } input[type=checkbox] { width:auto } button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible { outline:2px solid var(--ui-accent); outline-offset:3px }
</style><div id="root"></div><script type="module" src="/app.js"></script></html>`)
