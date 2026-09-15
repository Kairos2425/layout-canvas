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
  #legend { font: 11px ui-monospace, monospace; padding: 6px 0; opacity: .85; }
  .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
        margin-right: 4px; vertical-align: -1px; }
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
  <div id="bar">
    <button id="btnEdit" onclick="sessionOpen()">Edit mode</button>
    <button id="btnUndo" onclick="sessionUndo()" disabled>Undo</button>
    <button id="btnEnd" onclick="sessionClose()" disabled>End session</button>
    <span id="rev" style="font-size:11px;align-self:center;opacity:.7"></span>
  </div>
</div>
<div id="right">
  <h2>Layout preview</h2>
  <div id="canvas">Press Preview</div>
  <div id="legend"></div>
  <h2>Result</h2>
  <pre id="out">—</pre>
</div>
<script>
const irBox = document.getElementById('ir');
const out = document.getElementById('out');
const canvas = document.getElementById('canvas');
let sessionOn = false, revision = 0, lastData = null, selInst = null;

function setSessionUI(on, rev) {
  sessionOn = on; revision = rev ?? revision;
  document.getElementById('btnEdit').disabled = on;
  document.getElementById('btnUndo').disabled = !on;
  document.getElementById('btnEnd').disabled = !on;
  document.getElementById('rev').textContent =
    on ? 'session rev ' + revision + (selInst ? ' | selected: ' + selInst : '') : '';
}

async function sessionOpen() {
  let ir;
  try { ir = JSON.parse(irBox.value); }
  catch (e) { out.innerHTML = '<span class="err">IR JSON parse error: ' + e + '</span>'; return; }
  const r = await fetch('/api/session/open', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ir_json: ir})
  });
  const res = await r.json();
  if (res.status !== 'ok') { out.innerHTML = '<span class="err">' + res.error + '</span>'; return; }
  selInst = null;
  setSessionUI(true, res.revision);
  out.textContent = 'session open — click an instance to select, click empty space to move it';
  call('preview');
}

async function sessionEdit(edits) {
  const r = await fetch('/api/session/edit', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({edits: edits, expected_revision: revision})
  });
  const res = await r.json();
  revision = res.revision ?? revision;
  if (res.status !== 'ok') {
    out.innerHTML = '<span class="err">' + JSON.stringify(res.diagnostics) + '</span>';
    setSessionUI(true, revision);
    return;
  }
  irBox.value = JSON.stringify(res.data.design, null, 2);
  setSessionUI(true, revision);
  call('preview');
}

async function sessionUndo() {
  const r = await fetch('/api/session/undo', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  const res = await r.json();
  revision = res.revision ?? revision;
  if (res.status === 'ok') irBox.value = JSON.stringify(res.data.design, null, 2);
  else out.innerHTML = '<span class="err">' + JSON.stringify(res.diagnostics) + '</span>';
  setSessionUI(true, revision);
  call('preview');
}

async function sessionClose() {
  await fetch('/api/session/close', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  selInst = null;
  setSessionUI(false);
  out.textContent = 'session closed';
}

// Click-to-edit: first click picks the smallest instance under the cursor,
// second click on empty space moves the selected instance origin there.
canvas.addEventListener('click', async (ev) => {
  if (!sessionOn || !lastData || !lastData.instances) return;
  const svg = canvas.querySelector('svg');
  if (!svg) return;
  const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(svg.getScreenCTM().inverse());
  const ly = lastData.bbox[3] - pt.y;  // y-flip back to layout space
  const lx = pt.x;
  let hit = null, hitArea = Infinity;
  for (const [id, b] of Object.entries(lastData.instances)) {
    if (lx >= b[0] && lx <= b[2] && ly >= b[1] && ly <= b[3]) {
      const a = (b[2]-b[0]) * (b[3]-b[1]);
      if (a < hitArea) { hitArea = a; hit = id; }
    }
  }
  if (hit) { selInst = hit; setSessionUI(true, revision); out.textContent = 'selected ' + hit + ' — click empty space to move'; }
  else if (selInst) {
    await sessionEdit([{op: 'set_placement', instance: selInst, x: lx, y: ly, relative_to: null}]);
    selInst = null;
    setSessionUI(true, revision);
  }
});

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
  if (action === 'preview') {
    canvas.innerHTML = res.data.svg;
    lastData = res.data;
    const lg = document.getElementById('legend');
    lg.innerHTML = Object.entries(res.data.layers || {}).map(
      ([l, m]) => '<span class="sw" style="background:' + m.color + '"></span>' + l
    ).join(' &nbsp; ')
    + (res.data.truncated ? ' &nbsp; <span class="err">(truncated)</span>' : '');
  }
  if (action === 'netlist') out.textContent = res.data.spice;
  else out.textContent = JSON.stringify(res.data, null, 2);
}

loadSample();
</script>
</body>
</html>
"""
