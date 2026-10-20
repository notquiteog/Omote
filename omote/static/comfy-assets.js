import { $, api, el } from './dom.js';

const prefix = '/api/media/comfyui/assets';
const busy = new Set(['downloading', 'verifying', 'installing']);
const bytes = n => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GiB` : n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MiB` : `${(n / 1024).toFixed(1)} KiB`;
let wired = false;

export function wireComfyAssets({ onUse }) {
  if (wired) return;
  wired = true;
  const open = el('button', 'ghost small', 'Models & workflows');
  open.id = 'studio-assets'; open.type = 'button';
  $('.studio-workspace-bar').append(open);
  const dialog = el('dialog'); dialog.id = 'comfy-assets-dialog';
  dialog.setAttribute('aria-labelledby', 'ca-title');
  dialog.innerHTML = `
    <header class="lm-heading"><h2 id="ca-title">ComfyUI models & workflows</h2><button id="ca-close" class="ghost" aria-label="Close model library">✕</button></header>
    <p class="lm-intro">Download once, create locally. Cancel keeps your progress so you can resume later.</p>
    <div class="lm-toolbar"><label>ComfyUI <select id="ca-provider" aria-label="ComfyUI connection"></select></label><button id="ca-refresh" class="ghost small">Refresh</button></div>
    <p id="ca-status" class="meta" role="status" aria-live="polite"></p>
    <div id="ca-assets"></div>
    <details class="lm-advanced"><summary>ComfyUI setup & storage</summary>
      <p class="meta">Omote’s local studio connects these folders automatically. For a separately managed ComfyUI installation, use the configuration below. A remote server needs the files on its own host.</p>
      <p id="ca-path" class="meta"></p><p id="ca-config" class="meta"></p>
      <a class="ghost small" href="/api/media/comfyui/assets/paths-config" download="omote-model-paths.yaml">Download paths configuration</a>
      <p class="meta">For a manual installation, start ComfyUI with <code>--extra-model-paths-config /path/to/omote-model-paths.yaml</code>. Check compatibility after restarting.</p>
    </details>
    <details class="lm-advanced"><summary>Add a model or workflow URL</summary><form id="ca-custom">
      <label>Type<select name="kind"><option value="safetensors">Model · safetensors</option><option value="workflow">Workflow · JSON</option></select></label>
      <label hidden>Workflow output<select name="media_kind" disabled><option value="image">Image</option><option value="video">Video</option></select></label>
      <label>Label<input name="label" required maxlength="100"></label>
      <label>Model folder<select name="category"><option>checkpoints</option><option>loras</option><option>vae</option><option>text_encoders</option><option>diffusion_models</option><option>controlnet</option><option>model_patches</option><option>clip_vision</option><option>upscale_models</option></select></label>
      <label>Model filename<input name="filename" placeholder="Optional, e.g. my-model.safetensors" pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{0,149}\\.safetensors"></label>
      <label>Public HTTPS URL<input name="url" type="url" required maxlength="2048"></label>
      <label>Exact size in bytes<input name="size" type="number" min="1" max="1099511627776" required></label>
      <label>SHA-256<input name="sha256" pattern="[a-f0-9]{64}" required maxlength="64" spellcheck="false"></label>
      <p class="meta">Use the publisher’s pinned revision, byte size and SHA-256. Workflows are JSON data; custom nodes are checked, never installed automatically.</p>
      <button class="primary" type="submit">Add to library</button>
    </form></details>`;
  document.body.append(dialog);
  let timer, inFlight = false, pending = false, epoch = 0, fingerprint = '', latest = null, message = '';
  const checks = new Map();
  const active = () => dialog.open && !document.hidden;
  function stop() { clearTimeout(timer); }
  function schedule() { stop(); if (active()) timer = setTimeout(refresh, 2000); }
  async function request(path = '', method = 'GET', body) {
    const response = await api(prefix + path, { method, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
    if (!response) throw new Error('The daemon is unavailable.');
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'This action could not be completed.');
    return result;
  }
  async function change(action) {
    if (pending) return;
    pending = true; epoch++; stop(); message = 'Working…';
    if (latest) paint(latest);
    try { await action(); } catch (error) { message = error.message; }
    finally { pending = false; fingerprint = ''; if (dialog.open) refresh(); }
  }
  function button(parent, label, key, handler, disabled = false) {
    const b = el('button', 'ghost small', label); b.type = 'button'; b.dataset.key = key;
    b.disabled = pending || disabled; b.onclick = () => change(handler); parent.append(b);
  }
  const provider = () => {
    const value = $('#ca-provider').value;
    if (!value) throw new Error('Connect ComfyUI in Settings → Connections, then refresh.');
    return value;
  };
  async function use(id) {
    const result = await request(`/${id}/use`, 'POST', { provider: provider() });
    if (result.model) { await onUse(result); dialog.close(); }
    else { message = 'Saved in Advanced · ComfyUI → Saved workflows. Open it in the editor to select its models.'; document.dispatchEvent(new Event('omote:workflows-changed')); }
  }
  async function check(id) {
    const result = await request(`/${id}/compatibility?provider=${encodeURIComponent(provider())}`);
    checks.set(id, result);
    message = result.valid ? 'This connection can find the required models and nodes. Loading the model validates its architecture.' : result.errors.join(' ');
  }
  function paint(data) {
    $('#ca-provider').disabled = pending;
    $('#ca-refresh').disabled = pending;
    const focused = dialog.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    const rows = $('#ca-assets'); rows.replaceChildren();
    const transferring = data.assets.some(row => busy.has(row.state));
    for (const row of data.assets) {
      const card = el('article', 'lm-card'); card.dataset.id = row.id;
      const heading = el('div', 'lm-heading');
      heading.append(el('h3', '', row.label), el('span', 'meta', row.state === 'ready' ? 'Downloaded & verified' : row.state)); card.append(heading);
      card.append(el('p', 'meta', `${bytes(row.size)} · ${row.kind === 'workflow' ? 'Workflow JSON' : row.category}`));
      if (row.note) card.append(el('p', 'meta', row.note));
      if (row.error) card.append(el('p', 'lm-problem', row.error));
      if (row.preflight.errors.length) card.append(el('p', 'lm-problem', row.preflight.errors.join(' ')));
      if (busy.has(row.state) || (row.downloaded > 0 && row.state !== 'ready')) {
        const progress = el('progress'); progress.max = row.size; progress.value = row.downloaded; progress.setAttribute('aria-label', `${row.label} download`);
        card.append(progress, el('p', 'meta', `${bytes(row.downloaded)} / ${bytes(row.size)}`));
      }
      const actions = el('div', 'lm-actions');
      if (busy.has(row.state)) button(actions, 'Cancel download', `cancel-${row.id}`, async () => { await request(`/${row.id}/cancel`, 'POST'); message = 'Progress kept. Resume whenever you’re ready.'; });
      else if (row.state !== 'ready') button(actions, row.downloaded ? 'Resume download' : 'Download', `download-${row.id}`, async () => { await request(`/${row.id}/download`, 'POST'); message = 'Downloading…'; }, transferring || !row.preflight.ok);
      else if (row.kind === 'workflow') button(actions, 'Check & use workflow', `use-${row.id}`, () => use(row.id));
      else button(actions, 'Check connection', `check-${row.id}`, () => check(row.id));
      if (row.kind === 'workflow' && row.state === 'ready') {
        const link = el('a', 'ghost small', 'Export JSON'); link.href = `${prefix}/${row.id}/file`; link.download = `${row.id}.json`; actions.append(link);
      }
      if (row.downloaded > 0 || row.state === 'ready') button(actions, 'Remove', `remove-${row.id}`, async () => {
        if (!confirm(`Remove the managed download for ${row.label}? Workflows and files outside this library are kept.`)) { message = ''; return; }
        await request(`/${row.id}`, 'DELETE'); checks.delete(row.id); message = 'Managed download removed.';
      }, busy.has(row.state));
      const report = checks.get(row.id);
      if (report) card.append(el('p', report.valid ? 'meta' : 'lm-problem', [...report.errors, ...report.warnings].join(' ')));
      card.append(actions); rows.append(card);
    }
    const recipe = el('article', 'lm-card'); recipe.dataset.id = data.starter.id;
    recipe.append(el('h3', '', data.starter.label), el('p', 'meta', 'After downloading SD Turbo and connecting its model folder, use this four-step image recipe in Studio.'));
    const actions = el('div', 'lm-actions'); button(actions, 'Check & use in Studio', 'use-recipe', () => use(data.starter.id)); recipe.append(actions); rows.append(recipe);
    $('#ca-path').textContent = `Managed models: ${data.directory}`;
    $('#ca-config').textContent = `Configuration: ${data.paths_config}`;
    $('#ca-status').textContent = message || 'Choose a download, then check it against your ComfyUI connection.';
    if (focused) [...dialog.querySelectorAll('[data-key]')].find(node => node.dataset.key === focused)?.focus();
  }
  async function refresh() {
    if (!active()) return;
    if (inFlight || pending) { schedule(); return; }
    const generation = epoch; inFlight = true;
    try {
      const data = await request();
      if (generation !== epoch || !active()) return;
      latest = data;
      const next = JSON.stringify([data, message]);
      if (next !== fingerprint) { paint(data); fingerprint = next; }
    } catch (error) { if (generation === epoch && active()) $('#ca-status').textContent = error.message; }
    finally { inFlight = false; schedule(); }
  }
  async function connections() {
    const response = await api('/api/media/comfyui/editors');
    if (!response?.ok) throw new Error('Could not load ComfyUI connections.');
    const data = await response.json();
    const selected = $('#ca-provider').value || $('#comfy-provider').value || $('#studio-provider').value;
    const options = data.editors.map(entry => { const option = el('option', '', entry.label); option.value = entry.id; return option; });
    if (!options.length) { const option = el('option', '', 'Connect ComfyUI in Settings'); option.value = ''; options.push(option); }
    $('#ca-provider').replaceChildren(...options);
    if (data.editors.some(entry => entry.id === selected)) $('#ca-provider').value = selected;
  }
  open.onclick = async () => {
    dialog.showModal(); epoch++; fingerprint = ''; message = ''; checks.clear();
    try { await connections(); } catch (error) { message = error.message; }
    refresh();
  };
  $('#ca-close').onclick = () => dialog.close();
  dialog.onclose = () => { epoch++; stop(); };
  $('#ca-provider').onchange = () => { checks.clear(); message = ''; fingerprint = ''; refresh(); };
  $('#ca-refresh').onclick = async () => { checks.clear(); fingerprint = ''; try { await connections(); } catch (error) { message = error.message; } refresh(); };
  const form = $('#ca-custom');
  form.elements.kind.onchange = () => {
    form.elements.media_kind.disabled = form.elements.kind.value !== 'workflow';
    form.elements.media_kind.closest('label').hidden = form.elements.kind.value !== 'workflow';
    for (const name of ['category', 'filename']) { form.elements[name].disabled = form.elements.kind.value === 'workflow'; form.elements[name].closest('label').hidden = form.elements.kind.value === 'workflow'; }
  };
  form.onsubmit = event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form)); data.size = Number(data.size);
    change(async () => { await request('', 'POST', data); form.reset(); form.elements.kind.onchange(); message = 'Added to your library. Download when ready.'; });
  };
  document.addEventListener('visibilitychange', () => { stop(); if (active()) refresh(); });
}
