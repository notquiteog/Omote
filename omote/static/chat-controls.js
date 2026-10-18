/* Model and reasoning choices beside the chat composer. */
import { $, api, el, json } from './dom.js';

const levels = ['', 'off', 'low', 'medium', 'high', 'xhigh', 'max'];
const labels = ['Auto', 'Off', 'Low', 'Medium', 'High', 'Extra high', 'Max'];
const catalogs = new Map();
const loading = new Set();
let providers = [];
let selected = readPreference();
let changing = false;
let busy = false;
let hooks;
const currentSession = () => hooks?.sessionId();

function readPreference() {
  try {
    const value = JSON.parse(localStorage.getItem('omote.chat-model') || 'null');
    return value && typeof value.provider === 'string' && typeof value.model === 'string' ? value : null;
  } catch { return null; }
}

export function preferredModel() { return selected || {}; }

function rememberModel(choice) {
  selected = choice;
  try {
    if (choice) localStorage.setItem('omote.chat-model', JSON.stringify(choice));
    else localStorage.removeItem('omote.chat-model');
  } catch { /* Live choices still work without browser storage. */ }
}

function key(choice) { return choice ? JSON.stringify([choice.provider, choice.model]) : ''; }

function renderModels() {
  const picker = $('#chat-model');
  picker.textContent = '';
  const fallback = el('option', '', 'Default model');
  fallback.value = '';
  fallback.disabled = Boolean(currentSession());
  picker.append(fallback);
  let found = false;
  for (const provider of providers) {
    const group = el('optgroup');
    group.label = provider.label || provider.id;
    for (const model of catalogs.get(provider.id) || []) {
      const option = el('option', '', model.label || model.id);
      option.value = key({ provider: provider.id, model: model.id });
      if (option.value === key(selected)) found = true;
      group.append(option);
    }
    if (group.children.length) picker.append(group);
  }
  // A manually configured model remains usable if its catalog is unavailable.
  if (selected && !found) {
    const option = el('option', '', `${selected.model} · ${selected.provider}`);
    option.value = key(selected);
    picker.append(option);
  }
  picker.value = key(selected);
  picker.disabled = busy || changing;
  $('#model-chip').hidden = !providers.length && !selected;
  $('#effort-wrap').hidden = !providers.length && !currentSession();
}

export function configureChatControls(data) {
  const allowed = new Set(data.capabilities?.chat || []);
  providers = (data.providers || []).filter(provider => allowed.has(provider.id));
  if (!currentSession() && selected && !allowed.has(selected.provider)) rememberModel(null);
  renderModels();
  for (const provider of providers) {
    if (loading.has(provider.id)) continue;
    loading.add(provider.id);
    json(`/api/providers/${encodeURIComponent(provider.id)}/models?modality=chat`).then(result => {
      if (result) catalogs.set(provider.id, (result.models || []).filter(model => model.id
        && (!model.capabilities?.length || model.capabilities.includes('completion'))));
      else catalogs.delete(provider.id);
      renderModels();
    }).finally(() => loading.delete(provider.id));
  }
}

export function syncChatControls(info) {
  if (info.model) {
    const provider = info.provider || selected?.provider
      || providers.find(p => catalogs.get(p.id)?.some(m => m.id === info.model))?.id;
    if (provider) selected = { provider, model: info.model };
  }
  if ('effort' in info) {
    $('#effort').value = String(Math.max(0, levels.indexOf(info.effort || '')));
    renderEffort();
  }
  renderModels();
}

export function chatControlsBusy(value) {
  busy = value;
  $('#chat-model').disabled = busy || changing;
}

function renderEffort() {
  const index = Number($('#effort').value);
  $('#effort-value').textContent = labels[index];
  $('#effort').setAttribute('aria-valuetext', labels[index]);
}

export function wireChatControls(callbacks) {
  hooks = callbacks;
  let saved = '';
  try { saved = localStorage.getItem('omote.effort') || ''; } catch { /* no storage */ }
  $('#effort').value = String(Math.max(0, levels.indexOf(saved)));
  renderEffort();
  $('#effort').oninput = renderEffort;
  $('#effort').onchange = () => hooks.onEffort(levels[Number($('#effort').value)]);
  $('#chat-model').onchange = async () => {
    const raw = $('#chat-model').value;
    const pair = raw ? JSON.parse(raw) : null;
    const choice = pair ? { provider: pair[0], model: pair[1] } : null;
    const id = currentSession();
    if (!id) { rememberModel(choice); return; }
    if (!choice) return;
    const previous = selected;
    changing = true;
    renderModels();
    try {
      const response = await api(`/api/sessions/${encodeURIComponent(id)}/model`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(choice),
      });
      let result = null;
      try { result = await response?.json(); } catch { /* reported below */ }
      if (!response?.ok || !result?.model) {
        hooks.onError(result?.detail || 'Could not change the model. Try again.');
        selected = previous;
      } else if (currentSession() === id) {
        rememberModel({ provider: result.provider, model: result.model });
        hooks.onModel(result);
      }
    } finally {
      changing = false;
      renderModels();
    }
  };
  renderModels();
}
