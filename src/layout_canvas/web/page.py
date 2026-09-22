"""Single-page HTML for the layout canvas.

Three-pane product surface, analog-canvas style: block palette on the left,
the real polygon canvas in the center (pan/zoom/select/move), and a tabbed
inspector on the right (params / results / simulation / gallery). Every edit
goes through the DesignSession transaction boundary — the same envelope an
MCP agent sees.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Layout Canvas</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#2d6cdf">
<style>
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --line: #e2e5ea; --ink: #1c2330;
    --mut: #68758c; --acc: #2d6cdf; --acc-soft: #eaf1fd;
    --ok: #1a9e5c; --bad: #d94f4f; --warn: #b8801f;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: 'Segoe UI', system-ui, sans-serif;
         background: var(--bg); color: var(--ink); font-size: 13px;
         display: grid; grid-template-rows: 44px 1fr; height: 100vh; }
  #top { display: flex; align-items: center; gap: 8px; padding: 0 14px;
         background: var(--panel); border-bottom: 1px solid var(--line); }
  #top .logo { font-weight: 650; font-size: 14px; letter-spacing: .2px; }
  #top .logo em { color: var(--acc); font-style: normal; }
  #top .spacer { flex: 1; }
  .btn { background: var(--panel); color: var(--ink); border: 1px solid var(--line);
         border-radius: 6px; padding: 5px 11px; font-size: 12px; cursor: pointer; }
  .btn:hover { border-color: var(--acc); color: var(--acc); }
  .btn.primary { background: var(--acc); border-color: var(--acc); color: #fff; }
  .btn.primary:hover { filter: brightness(1.08); }
  .btn:disabled { opacity: .45; cursor: default; }
  #rev { font: 11px ui-monospace, monospace; color: var(--mut); }

  #main { display: grid; grid-template-columns: 220px 1fr 300px; min-height: 0; }
  aside { background: var(--panel); overflow-y: auto; min-height: 0; }
  #left { border-right: 1px solid var(--line); padding: 10px; }
  #right { border-left: 1px solid var(--line); display: flex; flex-direction: column; }
  aside h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .8px;
             color: var(--mut); margin: 12px 0 6px; }
  aside h3:first-child { margin-top: 0; }

  .blk { border: 1px solid var(--line); border-radius: 7px; padding: 7px 9px;
         margin-bottom: 6px; cursor: pointer; }
  .blk:hover { border-color: var(--acc); background: var(--acc-soft); }
  .blk b { display: block; font-size: 12px; }
  .blk span { font-size: 10.5px; color: var(--mut); }

  #stage { position: relative; overflow: hidden; background:
      radial-gradient(circle, #d9dde4 1px, transparent 1px) 0 0/22px 22px, var(--bg); }
  #canvas { position: absolute; inset: 0; display: flex; align-items: center;
            justify-content: center; }
  #canvas svg { max-width: 92%; max-height: 92%; cursor: grab;
                filter: drop-shadow(0 2px 10px rgba(30,40,60,.18));
                background: #fff; border-radius: 4px; }
  #hint { position: absolute; left: 12px; bottom: 10px; font-size: 11px;
          color: var(--mut); background: rgba(255,255,255,.85);
          padding: 3px 9px; border-radius: 5px; border: 1px solid var(--line); }
  #legend { position: absolute; right: 12px; bottom: 10px; font: 10.5px ui-monospace;
            background: rgba(255,255,255,.9); padding: 5px 9px; border-radius: 5px;
            border: 1px solid var(--line); }
  .sw { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
        margin: 0 4px 0 8px; vertical-align: -1px; }

  .tabs { display: flex; border-bottom: 1px solid var(--line); }
  .tab { flex: 1; text-align: center; padding: 8px 0; font-size: 11.5px;
         color: var(--mut); cursor: pointer; border-bottom: 2px solid transparent; }
  .tab.on { color: var(--ink); border-bottom-color: var(--acc); font-weight: 600; }
  #pane { flex: 1; overflow-y: auto; padding: 12px; }

  .fld { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
  .fld label { width: 84px; font-size: 11px; color: var(--mut); flex: none;
               overflow: hidden; text-overflow: ellipsis; }
  .fld input, .fld select { flex: 1; min-width: 0; border: 1px solid var(--line);
         border-radius: 5px; padding: 4px 7px; font: 12px ui-monospace, monospace;
         background: #fbfbfc; }
  .fld input:focus { outline: none; border-color: var(--acc); }

  pre { background: #10141b; color: #c8d3e0; border-radius: 7px; padding: 10px;
        font: 11px/1.5 ui-monospace, monospace; overflow: auto; white-space: pre-wrap;
        max-height: 46vh; }
  .err { color: var(--bad); }
  .ok { color: var(--ok); }
  table.op { border-collapse: collapse; width: 100%; font: 11.5px ui-monospace; }
  table.op td, table.op th { border-bottom: 1px solid var(--line);
        padding: 4px 8px; text-align: right; }
  table.op th { color: var(--mut); font-weight: 600; text-align: left; }

  .card { border: 1px solid var(--line); border-radius: 8px; padding: 8px;
          margin-bottom: 8px; background: var(--panel); }
  .card b { font-size: 12px; }
  .card .m { font-size: 10.5px; color: var(--mut); margin: 2px 0 6px; }
  .card svg { width: 100%; border: 1px solid var(--line); border-radius: 4px;
              background: #fff; }
  details { border: 1px solid var(--line); border-radius: 7px; margin-top: 10px; }
  details summary { padding: 6px 10px; font-size: 11px; color: var(--mut);
                    cursor: pointer; }
  details textarea { width: 100%; height: 220px; border: 0; border-top: 1px solid
        var(--line); font: 10.5px ui-monospace; padding: 8px; background: #fbfbfc; }
</style>
</head>
<body>
<header id="top">
  <span class="logo">Layout<em>Canvas</em></span>
  <span id="rev"></span>
  <span class="spacer"></span>
  <button class="btn" onclick="sessionUndo()" title="undo last edit">Undo</button>
  <button class="btn" onclick="call('simulate',{analysis:'op'})">Simulate</button>
  <button class="btn" onclick="call('simulate',{analysis:'tran'})">Tran</button>
  <button class="btn" onclick="call('ppa')">PPA</button>
  <button class="btn" onclick="call('connectivity')">Conn</button>
  <button class="btn" onclick="call('netlist')">Netlist</button>
  <button class="btn" onclick="call('drc')">DRC</button>
  <button class="btn" onclick="call('virtuoso')">Virtuoso</button>
  <button class="btn primary" onclick="publish()">Publish</button>
</header>

<div id="main">
<aside id="left">
  <h3>Blocks</h3>
  <div id="palette"></div>
  <h3>Layers</h3>
  <div id="layerList" style="font:10.5px ui-monospace;color:var(--mut)"></div>
</aside>

<div id="stage">
  <div id="canvas"></div>
  <div id="legend"></div>
  <div id="hint">click a block to select · click empty space to move it · wheel to zoom</div>
</div>

<aside id="right">
  <div class="tabs">
    <div class="tab on" data-t="inspector" onclick="tab('inspector')">Inspector</div>
    <div class="tab" data-t="output" onclick="tab('output')">Output</div>
    <div class="tab" data-t="sim" onclick="tab('sim')">Sim</div>
    <div class="tab" data-t="gallery" onclick="tab('gallery');listGallery()">Gallery</div>
  </div>
  <div id="pane"></div>
</aside>
</div>

<details id="irdetails" style="position:fixed;right:8px;bottom:8px;width:380px;
         background:var(--panel);z-index:9">
  <summary>design IR</summary>
  <textarea id="ir" spellcheck="false"></textarea>
</details>

<script>
const irBox = document.getElementById('ir');
const canvas = document.getElementById('canvas');
const pane = document.getElementById('pane');
let revision = 0, lastData = null, selInst = null, blocks = [];
let view = null;  // {x,y,w,h} viewBox state for pan/zoom
let curTab = 'inspector';

function setRev() {
  document.getElementById('rev').textContent =
    'rev ' + revision + (selInst ? ' · ' + selInst : '');
}

function tab(t) {
  curTab = t;
  document.querySelectorAll('.tab').forEach(e =>
    e.classList.toggle('on', e.dataset.t === t));
  renderPane();
}

function renderPane() {
  if (curTab === 'inspector') return renderInspector();
  if (curTab === 'sim') return;  // sim pane keeps its own DOM
  if (curTab === 'gallery') return;  // gallery renders itself
  // output
  pane.innerHTML = '<pre id="out">—</pre>';
}

function ir() { return JSON.parse(irBox.value); }

async function api(action, payload) {
  const r = await fetch('/api/' + action, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload || {})});
  return r.json();
}

// ---------- session ----------
async function sessionOpen(design) {
  const res = await api('session/open', {ir_json: design});
  if (res.status !== 'ok') { showErr(res); return false; }
  revision = res.revision; setRev();
  irBox.value = JSON.stringify(res.data ? res.data.design : design, null, 2);
  return true;
}

async function sessionEdit(edits) {
  const res = await api('session/edit',
    {edits: edits, expected_revision: revision});
  revision = res.revision ?? revision;
  if (res.status !== 'ok') { showErr(res); setRev(); return false; }
  irBox.value = JSON.stringify(res.data.design, null, 2);
  setRev(); refresh(); return true;
}

async function sessionUndo() {
  const res = await api('session/undo', {});
  revision = res.revision ?? revision;
  if (res.status === 'ok') irBox.value = JSON.stringify(res.data.design, null, 2);
  else showErr(res);
  setRev(); refresh();
}

// ---------- canvas ----------
async function refresh() {
  const res = await api('preview', {ir_json: ir()});
  if (res.status !== 'ok') { showErr(res); return; }
  lastData = res.data;
  canvas.innerHTML = res.data.svg;
  const lg = document.getElementById('legend');
  lg.innerHTML = Object.entries(res.data.layers || {}).map(
    ([l, m]) => '<span class="sw" style="background:' + m.color + '"></span>' + l
  ).join('') + (res.data.truncated ? ' · truncated' : '');
  const ll = document.getElementById('layerList');
  ll.innerHTML = Object.keys(res.data.layers || {})
    .map(l => '<div>' + l + '</div>').join('');
  const svg = canvas.querySelector('svg');
  if (svg) { svg.removeAttribute('width'); svg.removeAttribute('height');
             if (!view) view = svg.viewBox.baseVal; }
  highlightSel();
}

function svgPt(ev) {
  const svg = canvas.querySelector('svg');
  if (!svg) return null;
  const pt = new DOMPoint(ev.clientX, ev.clientY)
    .matrixTransform(svg.getScreenCTM().inverse());
  return {x: pt.x, y: (lastData.bbox ? lastData.bbox[3] - pt.y : pt.y)};
}

function hitInst(lx, ly) {
  let hit = null, area = Infinity;
  for (const [id, b] of Object.entries(lastData.instances || {})) {
    if (lx >= b[0] && lx <= b[2] && ly >= b[1] && ly <= b[3]) {
      const a = (b[2]-b[0]) * (b[3]-b[1]);
      if (a < area) { area = a; hit = id; }
    }
  }
  return hit;
}

function highlightSel() {
  if (!selInst || !lastData || !lastData.instances[selInst]) return;
  const svg = canvas.querySelector('svg');
  const b = lastData.instances[selInst];
  if (!svg || !b) return;
  const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  r.setAttribute('x', b[0]);
  r.setAttribute('y', lastData.bbox[3] - b[3]);  // flip back to svg space
  r.setAttribute('width', b[2]-b[0]);
  r.setAttribute('height', b[3]-b[1]);
  r.setAttribute('fill', 'none');
  r.setAttribute('stroke', '#2d6cdf');
  r.setAttribute('stroke-width', '0.12');
  r.setAttribute('stroke-dasharray', '0.25 0.15');
  svg.appendChild(r);
}

// select / move / pan / zoom
let drag = null;
canvas.addEventListener('mousedown', (ev) => {
  const svg = canvas.querySelector('svg');
  if (!svg) return;
  drag = {x: ev.clientX, y: ev.clientY, vb: {...svg.viewBox.baseVal}};
});
window.addEventListener('mousemove', (ev) => {
  const svg = canvas.querySelector('svg');
  if (!drag || !svg) return;
  const vb = svg.viewBox.baseVal;
  const sx = vb.width / svg.clientWidth;
  vb.x = drag.vb.x - (ev.clientX - drag.x) * sx;
  vb.y = drag.vb.y - (ev.clientY - drag.y) * sx;
});
window.addEventListener('mouseup', () => { drag = null; });
canvas.addEventListener('wheel', (ev) => {
  const svg = canvas.querySelector('svg');
  if (!svg) return;
  ev.preventDefault();
  const vb = svg.viewBox.baseVal;
  const f = ev.deltaY > 0 ? 1.15 : 0.87;
  const pt = new DOMPoint(ev.clientX, ev.clientY)
    .matrixTransform(svg.getScreenCTM().inverse());
  vb.x = pt.x - (pt.x - vb.x) * f;
  vb.y = pt.y - (pt.y - vb.y) * f;
  vb.width *= f; vb.height *= f;
}, {passive: false});

canvas.addEventListener('click', async (ev) => {
  if (!lastData) return;
  const p = svgPt(ev); if (!p) return;
  const hit = hitInst(p.x, p.y);
  if (hit) { selInst = hit; setRev(); highlightSel(); renderInspector(); }
  else if (selInst) {
    await sessionEdit([{op: 'set_placement', instance: selInst,
                        x: p.x, y: p.y, relative_to: null}]);
    selInst = null; setRev();
  }
});

// ---------- inspector ----------
function renderInspector() {
  const d = ir();
  if (!selInst) {
    pane.innerHTML =
      '<h3 style="font-size:11px;text-transform:uppercase;color:var(--mut)">Design</h3>' +
      '<div style="font-size:12px"><b>' + d.name + '</b> · ' + d.pdk + '</div>' +
      '<div style="color:var(--mut);margin-top:4px">' + d.instances.length +
      ' instances · ' + (d.nets||[]).length + ' nets · ' +
      (d.ports||[]).length + ' ports</div>' +
      '<h3 style="font-size:11px;text-transform:uppercase;color:var(--mut);margin-top:14px">Instances</h3>' +
      d.instances.map(i => '<div class="blk" onclick="pick(\\''+i.id+'\\')">' +
        '<b>'+i.id+'</b><span>'+i.block+'</span></div>').join('');
    return;
  }
  const inst = d.instances.find(i => i.id === selInst);
  if (!inst) { selInst = null; return renderInspector(); }
  const spec = blocks.find(b => b.name === inst.block);
  const defs = {};   // param name -> spec entry
  if (spec && spec.params) spec.params.forEach(p => { defs[p.name] = p; });
  const fields = spec && spec.params ? spec.params.map(p => p.name) :
    Object.keys(inst.params || {});
  pane.innerHTML =
    '<h3 style="font-size:11px;text-transform:uppercase;color:var(--mut)">Instance</h3>' +
    '<div style="font-size:12px;margin-bottom:8px"><b>' + inst.id + '</b> ' +
    '<span style="color:var(--mut)">' + inst.block + '</span></div>' +
    fields.map(k => {
      const def = defs[k];
      const v = inst.params && inst.params[k] !== undefined ? inst.params[k]
                : (def && def.default !== undefined ? def.default : '');
      const num = !def || def.type !== 'str';
      return '<div class="fld"><label title="'+k+'">'+k+'</label>' +
        '<input data-p="'+k+'" value="'+v+'" ' +
        (num ? 'type="number" step="any"' : '') +
        ' onchange="setParam(this)"></div>';
    }).join('') +
    '<button class="btn" style="margin-top:6px" onclick="removeInst()">Remove instance</button>';
}

async function setParam(el) {
  const k = el.dataset.p;
  let v = el.value;
  if (el.type === 'number') v = parseFloat(v);
  await sessionEdit([{op: 'set_params', instance: selInst, params: {[k]: v}}]);
}

async function removeInst() {
  if (!selInst) return;
  await sessionEdit([{op: 'remove_instance', instance: selInst}]);
  selInst = null; setRev(); renderInspector();
}

function pick(id) { selInst = id; setRev(); highlightSel(); renderInspector(); }

// ---------- palette ----------
async function loadPalette() {
  const r = await fetch('/api/blocks');
  blocks = await r.json();
  document.getElementById('palette').innerHTML = blocks.map(b =>
    '<div class="blk" onclick="addBlock(\\''+b.name+'\\')"><b>' +
    b.name.split('.').pop() + '</b><span>' + b.name + '</span></div>').join('');
}

async function addBlock(name) {
  const spec = blocks.find(b => b.name === name);
  const params = {};
  if (spec && spec.params) spec.params.forEach(p => {
    if (p.default !== undefined) params[p.name] = p.default;
  });
  const id = name.split('.').pop().replace(/[^a-z0-9_]/gi, '_') + '_' +
             Math.floor(Math.random() * 900 + 100);
  await sessionEdit([{op: 'add_instance',
    instance: {id: id, block: name, params: params}}]);
  selInst = id; setRev();
}

// ---------- actions ----------
function showErr(res) {
  const msg = res.error || JSON.stringify(res.diagnostics || res, null, 2);
  if (curTab !== 'output') tab('output');
  const o = document.getElementById('out');
  if (o) o.innerHTML = '<span class="err">' + msg + '</span>';
}

async function call(action, extra) {
  const payload = {ir_json: ir()};
  Object.assign(payload, extra || {});
  const res = await api(action, payload);
  if (action === 'simulate') return showSim(res);
  if (res.status !== 'ok') return showErr(res);
  if (curTab !== 'output') tab('output');
  const o = document.getElementById('out');
  if (action === 'netlist') o.textContent = res.data.spice;
  else if (action === 'virtuoso')
    o.textContent = '=== SKILL ===\\n' + res.data.skill +
                    '\\n\\n=== SPECTRE ===\\n' + res.data.spectre;
  else o.textContent = JSON.stringify(res.data, null, 2);
}

// ---------- simulation ----------
function showSim(res) {
  tab('sim');
  const d = res.data || {};
  if (res.status !== 'ok' || d.status !== 'passed') {
    pane.innerHTML = '<pre><span class="err">' +
      (d.errors || res.error || JSON.stringify(d)).toString() +
      '</span></pre><pre>' + (d.stdout || '').slice(-2000) + '</pre>';
    return;
  }
  const waves = d.waves || {};
  const names = Object.keys(waves);
  let html = '<h3 style="font-size:11px;text-transform:uppercase;color:var(--mut)">' +
    'op result · ' + d.simulator + '</h3><table class="op"><tr><th>net</th>' +
    '<th>V</th></tr>';
  for (const n of names)
    html += '<tr><td>' + n + '</td><td>' +
      (waves[n][0] !== undefined ? waves[n][0].toPrecision(4) : '—') + '</td></tr>';
  html += '</table>';
  if ((d.sweep || []).length > 1) {
    html += '<h3 style="font-size:11px;text-transform:uppercase;color:var(--mut);margin-top:12px">transient</h3>';
    for (const n of names) html += sparkline(n, d.sweep, waves[n]);
  }
  html += '<details style="margin-top:10px"><summary>log</summary><pre>' +
    (d.stdout || '').slice(-4000) + '</pre></details>';
  pane.innerHTML = html;
}

function sparkline(name, xs, ys) {
  const w = 260, h = 54, pad = 4;
  if (!xs || xs.length < 2 || !ys.length) return '';
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pts = xs.map((x, i) =>
    (pad + (x - x0) / (x1 - x0 || 1) * (w - 2*pad)) + ',' +
    (h - pad - (ys[i] - y0) / (y1 - y0 || 1e-12) * (h - 2*pad))).join(' ');
  return '<div style="margin:8px 0"><div style="font:10.5px ui-monospace;' +
    'color:var(--mut)">v(' + name + ') · ' + y1.toPrecision(3) + 'V max</div>' +
    '<svg width="'+w+'" height="'+h+'" style="background:#fff;border:1px solid' +
    ' var(--line);border-radius:6px"><polyline points="'+pts+'" fill="none"' +
    ' stroke="#2d6cdf" stroke-width="1.4"/></svg></div>';
}

// ---------- gallery ----------
async function publish() {
  const meta = {author: 'local', description: '', tags: []};
  const res = await api('gallery/publish', {ir_json: ir(), meta: meta});
  if (res.status !== 'ok') return showErr(res);
  tab('gallery'); listGallery();
}

async function listGallery() {
  const res = await api('gallery/list', {});
  if (res.status !== 'ok' || !res.data.entries.length) {
    pane.innerHTML = '<div style="color:var(--mut);padding:6px">' +
      'empty — publish a design to share it</div>';
    return;
  }
  const cards = [];
  for (const e of res.data.entries) {
    const det = await api('gallery/get', {id: e.id});
    const svg = det.status === 'ok' ? det.data.preview_svg : '';
    cards.push('<div class="card"><b>' + e.name + '</b>' +
      '<div class="m">' + e.pdk + ' · ' + e.instances + ' inst · ' +
      e.author + ' · ' + e.created.slice(0,10) + '</div>' + svg +
      '<button class="btn" style="margin-top:6px" ' +
      'onclick="openEntry(\\''+e.id+'\\')">Open</button></div>');
  }
  pane.innerHTML =
    '<button class="btn" style="margin-bottom:8px" onclick="syncGallery()">' +
    'Sync with remote</button>' + cards.join('');
}

async function openEntry(id) {
  const res = await api('gallery/fork', {id: id});
  if (res.status !== 'ok') return showErr(res);
  revision = res.revision; selInst = null; setRev();
  irBox.value = JSON.stringify(res.data.design, null, 2);
  refresh(); renderInspector();
}

async function syncGallery() {
  const remote = prompt('Gallery remote (git URL or path; blank = existing):', '');
  if (remote === null) return;
  const res = await api('gallery/sync', remote ? {remote: remote} : {});
  const o = JSON.stringify(res.data || res, null, 2);
  tab('output');
  document.getElementById('out').textContent = 'sync: ' + o;
  listGallery();
}

// ---------- boot ----------
async function boot() {
  const r = await fetch('/api/sample');
  const sample = await r.json();
  irBox.value = JSON.stringify(sample, null, 2);
  await loadPalette();
  await sessionOpen(sample);
  refresh(); renderInspector();
}
boot();
if ('serviceWorker' in navigator)
  navigator.serviceWorker.register('/sw.js').catch(() => {});
</script>
</body>
</html>
"""
