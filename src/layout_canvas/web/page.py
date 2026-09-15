"""Single-page HTML for the local review canvas."""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Layout Canvas — Review</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #171a18; color: #e8e4d8;
         display: grid; grid-template-columns: 420px 1fr; height: 100vh; }
  #left { display: flex; flex-direction: column; border-right: 1px solid #333; }
  header { padding: 12px 16px; background: #1e3d36; }
  header h1 { margin: 0; font-size: 16px; }
  header p { margin: 2px 0 0; font-size: 11px; opacity: .7; }
  textarea { flex: 1; background: #101312; color: #d8d4c8; border: 0; padding: 12px;
             font: 12px/1.5 ui-monospace, monospace; resize: none; }
  #bar { display: flex; gap: 6px; padding: 8px; background: #1d201e; flex-wrap: wrap; }
  button { background: #1e3d36; color: #e8e4d8; border: 1px solid #3a5c52; border-radius: 4px;
           padding: 5px 12px; font-size: 12px; cursor: pointer; }
  button:hover { background: #2a5549; }
  #right { overflow: auto; padding: 16px; }
  #canvas { background: #101312; border: 1px solid #333; min-height: 300px; display: flex;
            align-items: center; justify-content: center; }
  #canvas svg { max-width: 100%; height: auto; }
  pre { background: #101312; border: 1px solid #333; padding: 10px; font-size: 11px;
        overflow: auto; white-space: pre-wrap; }
  h2 { font-size: 13px; margin: 18px 0 6px; color: #9fc5b8; }
  .err { color: #ff7a7a; }
</style>
</head>
<body>
<div id="left">
  <header><h1>Layout Canvas</h1><p>Local-first Block IR review — edit, compile, inspect</p></header>
  <textarea id="ir" spellcheck="false" placeholder="Paste Block IR JSON here…"></textarea>
  <div id="bar">
    <button onclick="loadSample()">Sample</button>
    <button onclick="call('preview')">Preview</button>
    <button onclick="call('ppa')">PPA</button>
    <button onclick="call('connectivity')">Connectivity</button>
    <button onclick="call('abstract')">Abstract</button>
    <button onclick="call('netlist')">Netlist</button>
  </div>
</div>
<div id="right">
  <h2>Layout preview</h2>
  <div id="canvas">Press Preview</div>
  <h2>Result</h2>
  <pre id="out">—</pre>
</div>
<script>
const irBox = document.getElementById('ir');
const out = document.getElementById('out');
const canvas = document.getElementById('canvas');

async function loadSample() {
  const r = await fetch('/api/sample');
  irBox.value = JSON.stringify(await r.json(), null, 2);
}

async function call(action) {
  let ir;
  try { ir = JSON.parse(irBox.value); }
  catch (e) { out.innerHTML = '<span class="err">IR JSON parse error: ' + e + '</span>'; return; }
  out.textContent = '…';
  const r = await fetch('/api/' + action, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ir_json: ir})
  });
  const res = await r.json();
  if (res.status !== 'ok') { out.innerHTML = '<span class="err">' + res.error + '</span>'; return; }
  if (action === 'preview') canvas.innerHTML = res.data.svg;
  if (action === 'netlist') out.textContent = res.data.spice;
  else out.textContent = JSON.stringify(res.data, null, 2);
}

loadSample();
</script>
</body>
</html>
"""
