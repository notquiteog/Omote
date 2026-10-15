import { $, api, el } from './dom.js';

let draft = null;
let saved = null;
let dirty = false;
let catalog = {};
let bridgeReady = false;
let poll = null;
let listEpoch = 0;
const pending = new Map();
const prefix = '/api/media/comfyui';

async function request(path, method = 'GET', body) {
  const response = await api(prefix + path, { method, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  if (!response) throw new Error('The daemon is unavailable.');
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function note(message) { $('#wb-status').textContent = message; }
function provider() {
  const value = $('#comfy-provider').value;
  if (!value) throw new Error('Select a ComfyUI connection first.');
  return encodeURIComponent(value);
}
function jsonDownload(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = el('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function markDirty() { dirty = true; $('#wb-dirty').textContent = 'Unsaved changes'; }
function checkedDraft(value) {
  const result = { graph: value.graph ?? null, prompt: value.prompt ?? null };
  const object = (item) => item !== null && typeof item === 'object' && !Array.isArray(item);
  if (!result.graph && !result.prompt) throw new Error('Choose native ComfyUI workflow JSON or an executable API prompt.');
  if (result.graph !== null && (!object(result.graph) || !Array.isArray(result.graph.nodes) || result.graph.nodes.length > 5000)) {
    throw new Error('Editor workflow JSON must contain a nodes array with at most 5000 nodes.');
  }
  if (result.prompt !== null) {
    if (!object(result.prompt) || !Object.keys(result.prompt).length || Object.keys(result.prompt).length > 5000) {
      throw new Error('An API prompt must contain 1–5000 nodes.');
    }
    for (const [id, node] of Object.entries(result.prompt)) {
      if (!object(node) || typeof node.class_type !== 'string' || !object(node.inputs)) {
        throw new Error(`Node ${id} needs class_type and inputs; export API format or capture from the editor.`);
      }
    }
  }
  if (new TextEncoder().encode(JSON.stringify([result.graph, result.prompt])).byteLength > 4 * 1024 * 1024) {
    throw new Error('Workflow exceeds 4 MB.');
  }
  return result;
}
function fill(value) {
  const candidate = checkedDraft(value);
  saved = value.id ? value : null;
  draft = candidate;
  $('#wb-name').value = value.name || '';
  $('#wb-project').value = value.project || '';
  $('#wb-notes').value = '';
  $('#wb-dirty').textContent = saved ? `Version ${saved.revision}` : 'Imported draft';
  dirty = !saved;
  note(saved ? `Loaded ${saved.name} · version ${saved.revision}.` : 'Workflow imported. Save a version to keep it.');
  $('#wb-comparison').replaceChildren();
  inspect();
}
async function list() {
  const epoch = ++listEpoch;
  const data = await request(`/workflows?q=${encodeURIComponent($('#wb-search').value)}`);
  if (epoch !== listEpoch) return;
  const box = $('#wb-list'); box.replaceChildren();
  for (const item of data.workflows) {
    const button = el('button', 'wb-saved'); button.type = 'button';
    button.append(el('strong', '', item.name), el('span', 'meta', `${item.project || 'Personal'} · v${item.revision}`));
    button.onclick = async () => {
      if (dirty && !confirm('Discard the unsaved workbench draft? Save a version first to keep it.')) return;
      try { fill(await request(`/workflows/${item.id}`)); await versions(); } catch (error) { note(error.message); }
    };
    box.appendChild(button);
  }
  if (!data.workflows.length) box.appendChild(el('p', 'meta', 'Import a workflow or capture the editor to start a version history.'));
  if (data.total > data.workflows.length) box.appendChild(el('p', 'meta', `Showing ${data.workflows.length} of ${data.total}. Narrow the search to find others.`));
}
async function versions() {
  const picker = $('#wb-version'); picker.replaceChildren();
  const baseline = $('#wb-compare-version'); baseline.replaceChildren();
  if (!saved) return;
  const data = await request(`/workflows/${saved.id}/versions`);
  for (const item of data.versions) {
    const option = el('option', '', `v${item.revision} · ${new Date(item.created * 1000).toLocaleString()}${item.notes ? ' · ' + item.notes : ''}`);
    option.value = item.revision; picker.appendChild(option);
    baseline.appendChild(option.cloneNode(true));
  }
  picker.value = saved.revision;
  baseline.value = data.versions.find(item => item.revision < saved.revision)?.revision || saved.revision;
}
async function save(copy = false) {
  if (!draft) throw new Error('Import or capture a workflow first.');
  const body = { ...draft, name: $('#wb-name').value, project: $('#wb-project').value,
    notes: $('#wb-notes').value, base_revision: saved?.revision };
  fill(await request(saved && !copy ? `/workflows/${saved.id}/versions` : '/workflows', 'POST', body));
  await Promise.all([list(), versions()]); note('Workflow version saved locally.');
}
function inspect() {
  const select = $('#wb-node'); select.replaceChildren();
  for (const [id, node] of Object.entries(draft?.prompt || {})) {
    const option = el('option', '', `${id} · ${node._meta?.title || node.class_type}`); option.value = id; select.appendChild(option);
  }
  $('#wb-node-count').textContent = draft?.prompt ? `${Object.keys(draft.prompt).length} executable nodes`
    : draft?.graph ? `${draft.graph.nodes.length} editor nodes · capture the API prompt to validate and run` : 'No workflow selected';
  fields();
}
function fields() {
  const box = $('#wb-inputs'); box.replaceChildren();
  const id = $('#wb-node').value;
  const node = draft?.prompt?.[id];
  if (!node) return;
  const spec = catalog[node.class_type];
  if (spec?.description) box.appendChild(el('p', 'meta', spec.description));
  for (const [name, value] of Object.entries(node.inputs)) {
    const label = el('label', 'wb-field'); label.appendChild(el('span', '', name));
    if (Array.isArray(value) || (value && typeof value === 'object')) {
      label.appendChild(el('code', '', JSON.stringify(value))); box.appendChild(label); continue;
    }
    const rule = spec?.input?.required?.[name] || spec?.input?.optional?.[name];
    let input;
    if (Array.isArray(rule?.[0])) {
      input = el('select');
      const options = rule[0].includes(value) ? rule[0] : [value, ...rule[0]];
      for (const choice of options) { const option = el('option', '', String(choice)); option.value = String(choice); input.appendChild(option); }
      input.value = String(value);
    } else if (typeof value === 'string' && (value.length > 70 || rule?.[1]?.multiline)) {
      input = el('textarea'); input.rows = 3; input.value = value;
    } else {
      input = el('input'); input.type = typeof value === 'boolean' ? 'checkbox' : typeof value === 'number' ? 'number' : 'text';
      if (input.type === 'checkbox') input.checked = value; else input.value = value ?? '';
      if (typeof value === 'number') input.step = rule?.[0] === 'INT' ? '1' : 'any';
    }
    input.onchange = () => {
      node.inputs[name] = typeof value === 'boolean' ? input.checked : typeof value === 'number' ? Number(input.value) : input.value;
      // An API edit cannot silently claim the editor's widget snapshot was updated too.
      draft.graph = null; markDirty();
      note('API input changed. Save a version to run it. Capture the editor again to save an editable graph snapshot.');
    };
    label.appendChild(input); box.appendChild(label);
  }
}
function bridge(action, payload = {}) {
  const frame = $('#comfy-frame');
  if (!frame.getAttribute('src')) return Promise.reject(new Error('Open the ComfyUI editor first.'));
  const origin = new URL(frame.src).origin;
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Editor bridge did not answer. Install the bridge, add this workspace origin in ComfyUI settings, and reconnect.')); }, 10000);
    pending.set(id, { origin, source: frame.contentWindow, resolve, reject, timer });
    frame.contentWindow.postMessage({ channel: 'omote.comfy.v1', id, action, ...payload }, origin);
  });
}
async function capture() {
  if (!bridgeReady) { await bridge('hello'); bridgeReady = true; }
  if (dirty && !confirm('Replace this unsaved draft with the current editor graph?')) return;
  const result = await bridge('capture');
  draft = checkedDraft(result);
  if (!$('#wb-name').value) $('#wb-name').value = 'Untitled workflow';
  markDirty(); inspect(); note('Captured the complete editor graph and executable prompt. Save a version to keep it.');
}
async function refreshRuns() {
  const data = await request('/runs');
  const box = $('#wb-runs'); box.replaceChildren();
  for (const run of data.runs.slice(0, 15)) {
    const row = el('div', 'wb-run');
    row.appendChild(el('span', 'meta', `${run.provider} · v${run.revision} · ${new Date(run.created * 1000).toLocaleString()}`));
    const state = el('p', '', 'Check status to inspect this run.'); row.appendChild(state);
    const buttons = el('div', 'creative-actions');
    const check = el('button', 'ghost small', 'Check status');
    const cancel = el('button', 'ghost small', 'Cancel queued');
    const keep = el('button', 'ghost small', 'Keep outputs'); keep.disabled = true;
    check.onclick = async () => {
      check.disabled = true;
      try {
        const current = await request(`/runs/${run.id}`);
        state.textContent = `${current.name} · ${current.state} · ${current.outputs.length} output files${current.error ? ' · ' + current.error : ''}`;
        keep.disabled = current.state !== 'done' || !current.outputs.length;
        cancel.disabled = current.state !== 'queued';
      } catch (error) { state.textContent = error.message; } finally { check.disabled = false; }
    };
    keep.onclick = async () => {
      keep.disabled = true;
      try {
        const result = await request(`/runs/${run.id}/import`, 'POST');
        state.textContent = `${result.asset_ids.length} assets in the library. ${result.errors.join(' ')}`;
        document.dispatchEvent(new CustomEvent('omote:library-changed'));
      } catch (error) { state.textContent = error.message; } finally { keep.disabled = false; }
    };
    cancel.onclick = async () => {
      try { await request(`/runs/${run.id}`, 'DELETE'); check.click(); } catch (error) { state.textContent = error.message; }
    };
    buttons.append(check, cancel, keep); row.appendChild(buttons); box.appendChild(row);
  }
  if (!data.runs.length) box.appendChild(el('p', 'meta', 'Runs queued from this workbench appear here and survive restarts.'));
}

export function showWorkflowLibrary() {
  if ($('#comfy-tools').hidden) $('#comfy-library-toggle').click();
}

export async function importWorkbenchFile(file) {
  try {
    if (file.size > 4 * 1024 * 1024) throw new Error('Workflow exceeds 4 MB.');
    if (dirty && !confirm('Replace the unsaved workflow draft?')) return false;
    const data = JSON.parse(await file.text());
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Workflow JSON must be an object.');
    const graph = Array.isArray(data.nodes) ? data : data.graph || data.workflow || null;
    const prompt = graph === data ? null : data.prompt || (!graph ? data : null);
    fill({ name: file.name.replace(/\.json$/i, ''), graph, prompt });
    $('#wb-version').replaceChildren(); $('#wb-compare-version').replaceChildren();
    note('Imported locally. Save a version before running.');
    return true;
  } catch (error) { note(error.message); return false; }
}

export function wireWorkbench() {
  const frame = $('#comfy-frame');
  const layout = el('div', 'comfy-layout');
  const aside = el('aside', 'workbench'); aside.id = 'comfy-tools';
  aside.hidden = true;
  aside.setAttribute('aria-label', 'Workflow library and version controls');
  aside.innerHTML = `<div class="creative-actions"><strong>Workflow workbench</strong><button id="wb-collapse" class="ghost small">Hide</button></div>
    <p id="wb-status" class="meta" role="status">Save versions, inspect inputs, and keep outputs with your characters.</p>
    <details open><summary>Saved workflows</summary><input id="wb-search" type="search" placeholder="Find a workflow…" aria-label="Find a workflow"><div id="wb-list"></div>
    <div class="creative-actions"><label class="chip">Import JSON<input id="wb-import" type="file" accept=".json,application/json" hidden></label><button id="wb-capture" class="ghost small">Capture editor</button></div></details>
    <label class="wb-field">Name<input id="wb-name" maxlength="150" placeholder="Character portrait · cinematic"></label>
    <label class="wb-field">Project<input id="wb-project" maxlength="300" placeholder="Story or project"></label>
    <label class="wb-field">Version note<input id="wb-notes" maxlength="4000" placeholder="What changed?"></label>
    <span id="wb-dirty" class="meta"></span>
    <div class="creative-actions"><button id="wb-save" class="primary small">Save version</button><button id="wb-copy" class="ghost small">Save copy</button><button id="wb-assistant" class="ghost small">Edit with chat</button></div>
    <label class="wb-field">Version<select id="wb-version" aria-label="Workflow version"></select></label>
    <div class="creative-actions"><button id="wb-load" class="ghost small">Load in editor</button><button id="wb-export" class="ghost small">Export JSON</button><button id="wb-delete" class="ghost small">Delete saved</button></div>
    <details><summary>Compare saved versions</summary><p class="meta">Review changes to the loaded saved version. Unsaved edits are excluded.</p><label class="wb-field">Compare from<select id="wb-compare-version" aria-label="Comparison baseline"></select></label><button id="wb-compare" class="ghost small">Compare versions</button><div id="wb-comparison" aria-live="polite"></div></details>
    <details open><summary>API node inspector</summary><p id="wb-node-count" class="meta"></p><select id="wb-node" aria-label="Inspect node"></select><div id="wb-inputs"></div>
    <div class="creative-actions"><button id="wb-catalog" class="ghost small">Load node schemas</button><button id="wb-validate" class="ghost small">Validate</button></div></details>
    <button id="wb-run" class="primary">Run saved version</button>
    <details><summary>Run history &amp; outputs</summary><button id="wb-refresh-runs" class="ghost small">Refresh runs</button><div id="wb-runs"></div></details>
    <details><summary>Connect editor bridge</summary><p class="meta">Install this optional extension in ComfyUI/custom_nodes. Restart ComfyUI, then add this exact origin to “Omote: trusted workspace origins” in its settings:</p><code id="wb-origin"></code>
    <div class="creative-actions"><a class="chip" href="/static/omote-comfyui-bridge.zip" download>Download bridge</a><button id="wb-bridge" class="ghost small">Connect bridge</button></div></details>`;
  const editor = el('div', 'comfy-editor-stage'); editor.id = 'comfy-editor-stage';
  const empty = el('div', 'comfy-editor-empty');
  const canvasIcon = el('div', 'comfy-canvas-icon', '◇'); canvasIcon.setAttribute('aria-hidden', 'true');
  const open = el('button', 'primary', 'Connect ComfyUI'); open.id = 'comfy-empty-action'; open.type = 'button';
  empty.append(canvasIcon, el('p', 'comfy-eyebrow', 'COMFYUI · NATIVE EDITOR'),
    el('h3', '', 'Room for your ideas.'),
    el('p', 'meta', 'Your complete node canvas, custom nodes, model library and workflow tabs. Open your ComfyUI installation and keep creating here.'), open,
    el('p', 'meta', 'Need saved versions or API inputs? Open Workflow library above.'));
  const loading = el('div', 'comfy-loading', 'Opening your ComfyUI workspace…'); loading.setAttribute('role', 'status');
  editor.appendChild(loading);
  frame.before(layout); editor.append(empty, frame); layout.append(aside, editor);
  const show = el('button', 'ghost small', 'Workflow library'); show.id = 'comfy-library-toggle';
  show.setAttribute('aria-expanded', 'false'); show.setAttribute('aria-controls', 'comfy-tools');
  $('#comfy-connect').after(show);
  const toggle = (visible) => { aside.hidden = !visible; show.setAttribute('aria-expanded', String(visible)); };
  show.onclick = () => { toggle(aside.hidden); if (!aside.hidden) list().catch((e) => note(e.message)); };
  $('#wb-collapse').onclick = () => { toggle(false); show.focus(); };
  $('#wb-origin').textContent = location.origin;
  const action = (id, fn) => { $(id).onclick = async (event) => { const button = event.currentTarget; button.disabled = true; try { await fn(); } catch (error) { note(error.message); } finally { button.disabled = false; } }; };
  action('#wb-save', () => save()); action('#wb-copy', () => save(true)); action('#wb-capture', capture);
  action('#wb-bridge', async () => { await bridge('hello'); bridgeReady = true; note('Editor bridge connected. Capture and load are available.'); });
  frame.addEventListener('load', () => { bridgeReady = false; });
  window.addEventListener('message', (event) => {
    const message = event.data;
    if (!message || message.channel !== 'omote.comfy.v1') return;
    const waiting = pending.get(message.id);
    if (!waiting || waiting.source !== event.source || waiting.origin !== event.origin) return;
    clearTimeout(waiting.timer); pending.delete(message.id);
    if (message.ok) waiting.resolve(message); else waiting.reject(new Error(message.error || 'Editor bridge failed'));
  });
  $('#wb-search').oninput = () => { clearTimeout(poll); poll = setTimeout(() => list().catch((e) => note(e.message)), 200); };
  $('#wb-name').oninput = markDirty; $('#wb-project').oninput = markDirty; $('#wb-notes').oninput = markDirty; $('#wb-node').onchange = fields;
  $('#wb-import').onchange = async (event) => {
    const file = event.target.files[0]; if (!file) return;
    try { await importWorkbenchFile(file); } finally { event.target.value = ''; }
  };
  $('#wb-version').onchange = async () => {
    if (!saved) return;
    if (dirty && !confirm('Discard the unsaved draft and load this version?')) { $('#wb-version').value = saved.revision; return; }
    try { fill(await request(`/workflows/${saved.id}?revision=${$('#wb-version').value}`)); } catch (error) { note(error.message); }
  };
  action('#wb-load', async () => {
    if (!draft?.graph && !draft?.prompt) throw new Error('Load or save a workflow first.');
    if (!confirm('Replace the graph currently open in ComfyUI? Capture it first to keep it.')) return;
    if (draft.graph) await bridge('load', { graph: draft.graph });
    else {
      const captured = await bridge('load_api', { prompt: draft.prompt });
      const canonical = checkedDraft(captured);
      draft.graph = canonical.graph;
      draft.prompt = canonical.prompt;
      markDirty();
      inspect();
    }
    note('Loaded in the native editor. Save this version to retain its current layout.');
  });
  action('#wb-assistant', () => {
    if (!saved || dirty) throw new Error('Save this version first so the chat edits the same workflow you see.');
    document.dispatchEvent(new CustomEvent('omote:edit-workflow', { detail: { id: saved.id, revision: saved.revision, name: saved.name } }));
  });
  action('#wb-export', () => { if (!draft) throw new Error('Select a workflow first.'); jsonDownload(draft.graph || draft.prompt, `${$('#wb-name').value || 'workflow'}.json`); });
  action('#wb-delete', async () => { if (!saved || !confirm('Delete this saved workflow and every local version?')) return; await request(`/workflows/${saved.id}`, 'DELETE'); saved = null; draft = null; dirty = false; for (const id of ['#wb-name', '#wb-project', '#wb-notes']) $(id).value = ''; $('#wb-version').replaceChildren(); $('#wb-compare-version').replaceChildren(); $('#wb-comparison').replaceChildren(); $('#wb-dirty').textContent = ''; inspect(); await list(); note('Saved workflow and its versions deleted.'); });
  action('#wb-catalog', async () => { catalog = (await request(`/connections/${provider()}/nodes`)).nodes; fields(); note(`Loaded ${Object.keys(catalog).length} installed node schemas.`); });
  action('#wb-validate', async () => {
    if (!draft?.prompt) throw new Error('Capture an API prompt from the editor or import API JSON first.');
    const result = await request(`/connections/${provider()}/validate`, 'POST', { prompt: draft.prompt });
    note(result.valid ? `Schema checks passed. ${result.warnings.join(' ')}` : [...result.errors, ...result.warnings].join('\n'));
  });
  action('#wb-run', async () => {
    if (!saved || dirty) throw new Error('Save the current draft as a version before running it.');
    await request('/runs', 'POST', { workflow_id: saved.id, revision: saved.revision, provider: decodeURIComponent(provider()) });
    note('Queued this saved version. Check Run history for progress and keep its outputs in your library.'); await refreshRuns();
  });
  action('#wb-compare', async () => {
    if (!saved) throw new Error('Save or select a workflow first.');
    const result = await request(`/workflows/${saved.id}/compare?before=${$('#wb-compare-version').value}&after=${saved.revision}`);
    const box = $('#wb-comparison'); box.replaceChildren();
    box.append(el('p', 'meta', `v${result.before_revision} → v${result.after_revision}: ${result.total} changes (${result.counts.added} added, ${result.counts.removed} removed, ${result.counts.changed} changed).`));
    for (const change of result.changes) {
      const row = el('details', 'wb-change');
      row.append(el('summary', '', `${change.kind} · ${change.path}`));
      for (const [label, value] of [['Before', change.before], ['After', change.after]]) {
        row.append(el('strong', '', label), el('pre', '', value === null ? '(absent)' : value));
      }
      box.append(row);
    }
    if (result.truncated) box.append(el('p', 'meta', 'Showing the first 250 changes. Export each version to review the complete workflows.'));
    note(`Compared saved versions ${result.before_revision} and ${result.after_revision}.`);
  });
  action('#wb-refresh-runs', refreshRuns);
  $('#studio-graph').addEventListener('click', () => { if (!aside.hidden) list().catch((e) => note(e.message)); });
  document.addEventListener('omote:workflows-changed', () => list().catch((e) => note(e.message)));
  window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
}
