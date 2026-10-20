import { $, api, el } from './dom.js';

const busyStates = new Set(['downloading', 'verifying', 'installing']);
const bytes = value => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GiB` : `${(value / 1024 ** 2).toFixed(0)} MiB`;

export function wireLocalModels({ onUse }) {
  const dialog = $('#local-models-dialog');
  let timer;
  let inFlight = false;
  let pending = false;
  let epoch = 0;
  let fingerprint = '';
  let latest;
  let failure = '';
  let providerState = '';
  const expanded = new Set();
  const mode = () => $('#local-models-device').value;
  const active = () => dialog.open && !document.hidden;
  const stop = () => { clearTimeout(timer); timer = null; };
  function schedule() { stop(); if (active()) timer = setTimeout(refresh, 2000); }

  async function request(path = '', options) {
    const response = await api(`/api/local-models${path}`, options);
    if (!response) throw new Error('The Omote daemon is not reachable.');
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'This change could not be completed.');
    return body;
  }

  async function change(path, method = 'POST', body = { mode: mode() }) {
    if (pending) return;
    pending = true;
    epoch++;
    stop();
    failure = '';
    $('#local-models-status').textContent = 'Working…';
    if (latest) render(latest);
    try {
      await request(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } catch (error) { failure = error.message; }
    finally { pending = false; fingerprint = ''; refresh(); }
  }

  function button(parent, label, action, id, handler, disabled = false) {
    const control = el('button', 'ghost small', label);
    control.type = 'button';
    control.dataset.action = action;
    control.dataset.model = id;
    control.disabled = pending || disabled;
    control.onclick = handler;
    parent.appendChild(control);
  }

  function card(row, data) {
    const card = el('article', 'lm-card');
    card.dataset.id = row.id;
    const heading = el('div', 'lm-heading');
    heading.append(el('h3', '', row.label), el('span', 'meta', ({
      ready: 'Downloaded', available: 'Not downloaded', downloading: 'Downloading', verifying: 'Checking',
      installing: 'Installing', cancelled: 'Paused', error: 'Needs attention',
    })[row.state] || row.state));
    card.appendChild(heading);
    const estimated = row.kind === 'model' ? ` · about ${bytes(mode() === 'gpu' ? row.preflight.estimated_vram_bytes : row.preflight.estimated_ram_bytes)} ${mode() === 'gpu' ? 'VRAM' : 'RAM'}` : '';
    card.appendChild(el('p', 'meta', `${bytes(row.size)} download${estimated}`));
    if (row.note) card.appendChild(el('p', 'meta', row.note));
    if (row.preflight.errors.length) card.appendChild(el('p', 'lm-problem', row.preflight.errors.join(' ')));
    if (row.preflight.warnings.length) card.appendChild(el('p', 'meta', row.preflight.warnings.join(' ')));
    if (row.error) card.appendChild(el('p', 'lm-problem', row.error));
    const progress = busyStates.has(row.state) || (row.downloaded > 0 && row.state !== 'ready');
    if (progress) {
      const bar = el('progress');
      bar.max = row.size;
      bar.value = Math.min(row.downloaded, row.size);
      bar.setAttribute('aria-label', `${row.label} download`);
      card.append(bar, el('p', 'meta', `${bytes(row.downloaded)} / ${bytes(row.size)} · ${row.state}`));
    }
    const actions = el('div', 'lm-actions');
    const anyDownload = [...data.models, data.runtime_download].filter(Boolean).some(item => busyStates.has(item.state));
    const settingUp = data.setup?.state === 'running';
    const owns = data.runtime.model_id === row.id;
    const running = owns && data.runtime.state === 'running';
    const starting = owns && data.runtime.state === 'starting';
    const unavailable = settingUp || anyDownload || ['starting', 'running', 'stopping'].includes(data.runtime.state);
    const details = el('details', 'lm-model-details'), manual = el('div', 'lm-actions');
    details.open = expanded.has(row.id);
    details.append(el('summary', '', 'Model details & controls'), manual);
    details.ontoggle = () => details.open ? expanded.add(row.id) : expanded.delete(row.id);
    const downloadActions = row.kind === 'model' ? manual : actions;
    if (row.kind === 'model' && !running && !starting && !(settingUp && data.setup.model_id === row.id)) {
      button(actions, row.state === 'ready' && data.runtime_installed ? 'Start model' : 'Set up & start', 'prepare', row.id,
        () => change(`/${row.id}/prepare`), !row.preflight.ok || unavailable);
      actions.lastChild.className = 'primary small';
    }
    if (busyStates.has(row.state) && !settingUp) {
      button(actions, 'Cancel download', 'cancel', row.id, () => change(`/${row.id}/cancel`));
    } else if (row.state !== 'ready' || (row.kind === 'runtime' && !data.runtime_installed)) {
      button(downloadActions, row.downloaded ? 'Resume' : row.kind === 'runtime' ? 'Install runtime' : 'Download', 'download', row.id,
        () => change(`/${row.id}/download`), !row.preflight.ok || anyDownload || settingUp);
    }
    if (row.kind === 'model' && row.state === 'ready') {
      if ((running || starting) && !settingUp) {
        button(actions, starting ? 'Cancel startup' : 'Stop', 'stop', row.id, () => change('/runtime/stop'));
      } else {
        button(manual, 'Start', 'start', row.id, () => change(`/${row.id}/start`),
          !data.runtime_installed || !row.preflight.ok || unavailable);
      }
      if (running) button(actions, 'Use in chat', 'use', row.id, async () => {
        if (pending) return;
        pending = true;
        try { await onUse(data.runtime.provider_id, row.id, row.label); dialog.close(); }
        catch (error) { failure = error.message; $('#local-models-status').textContent = failure; }
        finally { pending = false; }
      });
    }
    if (row.kind === 'model' && (row.downloaded || row.state === 'ready') && !busyStates.has(row.state)) {
      button(manual, 'Remove', 'remove', row.id, () => {
        if (confirm(`Remove ${row.label} from this computer? You can download it again later.`)) change(`/${row.id}`, 'DELETE');
      }, settingUp || (data.runtime.model_id === row.id && ['running', 'starting', 'stopping'].includes(data.runtime.state)));
    }
    card.appendChild(actions);
    if (row.kind === 'model') card.append(details);
    return card;
  }

  function render(data) {
    const shape = JSON.stringify([data, pending, mode()]);
    if (shape === fingerprint) return;
    fingerprint = shape;
    const nextProviderState = data.runtime.state === 'running' ? `running:${data.runtime.model_id}` : 'stopped';
    if (providerState !== nextProviderState) {
      providerState = nextProviderState;
      document.dispatchEvent(new Event('omote:providers-changed'));
    }
    const focused = document.activeElement?.dataset;
    const focus = focused?.action ? { action: focused.action, model: focused.model } : null;
    const setup = data.setup || {};
    const settingUp = setup.state === 'running';
    const setupPanel = $('#local-models-setup');
    setupPanel.replaceChildren();
    setupPanel.hidden = !settingUp && !['error', 'cancelled'].includes(setup.state);
    if (!setupPanel.hidden) {
      setupPanel.append(el('strong', '', setup.note || 'Preparing your model'), el('p', 'meta',
        setup.error || 'You can keep using Omote. Setup continues when you close this window.'));
      if (settingUp) {
        const downloading = [...data.models, data.runtime_download].find(row => row && busyStates.has(row.state));
        const bar = el('progress'); bar.setAttribute('aria-label', 'Model setup progress');
        if (downloading?.state === 'downloading') { bar.max = downloading.size; bar.value = downloading.downloaded; }
        setupPanel.append(bar);
        button(setupPanel, 'Cancel setup', 'cancel-setup', setup.model_id, () => change('/setup/cancel'));
      }
    }
    const holder = $('#local-models-runtime');
    if (data.runtime_download) {
      holder.replaceChildren(card(data.runtime_download, data));
      const status = el('p', 'lm-runtime-state', data.runtime.state === 'running'
        ? `Running on ${data.runtime.device} · ready for local chat`
        : data.runtime.state === 'starting' ? 'Loading model…' : data.runtime_installed ? 'Runtime installed · stopped' : 'Installed automatically when you set up a model.');
      holder.appendChild(status);
    } else holder.replaceChildren(el('p', 'lm-problem', 'No managed runtime is available for this platform yet.'));
    $('#local-models-list').replaceChildren(...data.models.map(row => card(row, data)));
    $('#local-models-storage').textContent = data.directory;
    $('#local-models-log').textContent = data.runtime.log_tail || 'No runtime logs yet.';
    $('#local-models-device').disabled = pending || settingUp || ['starting', 'running', 'stopping'].includes(data.runtime.state);
    $('#local-models-repair').disabled = pending || settingUp || !data.runtime_download || ['starting', 'running', 'stopping'].includes(data.runtime.state);
    $('#local-models-custom button').disabled = pending;
    $('#local-models-status').textContent = failure || data.runtime.error || 'Downloads use the network. Model inference stays on this computer.';
    if (focus) {
      const target = [...dialog.querySelectorAll('[data-action]')].find(node => node.dataset.action === focus.action && node.dataset.model === focus.model);
      target?.focus({ preventScroll: true });
    }
  }

  async function refresh() {
    stop();
    if (!active() || inFlight || pending) return;
    const version = epoch;
    inFlight = true;
    try {
      const data = await request(`?mode=${mode()}`);
      if (version !== epoch || !active()) return;
      latest = data;
      render(data);
    } catch (error) { if (version === epoch && active()) $('#local-models-status').textContent = error.message; }
    finally { inFlight = false; schedule(); }
  }

  $('#open-local-models').onclick = () => {
    $('#system-dialog').close();
    dialog.showModal();
    failure = ''; fingerprint = '';
    $('#local-models-status').textContent = 'Checking this computer…';
    refresh();
  };
  $('#local-models-close').onclick = () => dialog.close();
  $('#local-models-repair').onclick = () => change('/runtime/repair');
  dialog.addEventListener('close', () => { epoch++; stop(); });
  $('#local-models-refresh').onclick = () => { failure = ''; fingerprint = ''; refresh(); };
  $('#local-models-device').onchange = () => { epoch++; fingerprint = ''; refresh(); };
  $('#local-models-custom').onsubmit = async event => {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    body.size = Number(body.size);
    await change('/models', 'POST', body);
    if (!failure) { form.reset(); form.closest('details').open = false; }
  };
  document.addEventListener('visibilitychange', () => { epoch++; if (document.hidden) stop(); else refresh(); });
}
