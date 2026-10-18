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
let starting = false;
let hooks;
let defaultModel = null;
let requestedEffort = '';
let sliderLevels = ['', ...levels.slice(1)];
let sliderLabels = {};
const currentSession = () => hooks?.sessionId();
export const chatModelChanging = () => changing;

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
  const defaultRow = catalogs.get(defaultModel?.provider)?.find(m => m.id === defaultModel?.model);
  const fallback = el('option', '', defaultModel?.model
    ? `${defaultRow?.label || defaultModel.model} (default)` : 'Default model');
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
  picker.disabled = busy || changing || starting;
  picker.setAttribute('aria-busy', String(changing));
  picker.title = changing ? 'Switching model…' : picker.selectedOptions[0]?.textContent || 'Choose a model';
  $('#model-chip').hidden = !providers.length && !selected;
  $('#effort-wrap').hidden = !providers.length && !currentSession();
  const choice = selected || defaultModel;
  const provider = providers.find(p => p.id === choice?.provider) || (!choice ? providers[0] : null);
  const chip = $('#provider-chip');
  chip.hidden = !provider && !choice?.provider;
  chip.textContent = provider?.label || choice?.provider || '';
  chip.title = provider?.local ? 'running on this machine' : 'a remote provider';
  chip.classList.toggle('local', Boolean(provider?.local));
  renderEffort();
}

export function configureChatControls(data) {
  const allowed = new Set(data.capabilities?.chat || []);
  providers = (data.providers || []).filter(provider => allowed.has(provider.id));
  defaultModel = data.routes?.chat || null;
  if (!currentSession() && selected && !allowed.has(selected.provider)) rememberModel(null);
  renderModels();
  for (const provider of providers) {
    if (loading.has(provider.id)) continue;
    loading.add(provider.id);
    json(`/api/providers/${encodeURIComponent(provider.id)}/models?modality=chat`).then(result => {
      if (result) catalogs.set(provider.id, (result.models || []).filter(model => model.id
        && (!Array.isArray(model.capabilities) || !model.capabilities.length
          || model.capabilities.includes('completion') || model.capabilities.includes('tools'))));
      else catalogs.delete(provider.id);
      renderModels();
    }).finally(() => loading.delete(provider.id));
  }
}

export function syncChatControls(info) {
  if (info.model) {
    const provider = info.provider || (selected?.model === info.model ? selected.provider : null)
      || providers.find(p => catalogs.get(p.id)?.some(m => m.id === info.model))?.id;
    if (provider) selected = { provider, model: info.model };
  }
  if ('effort' in info) {
    requestedEffort = info.effort || '';
  }
  renderModels();
}

export function chatControlsBusy(value) {
  busy = value;
  $('#chat-model').disabled = busy || changing || starting;
}

export function chatControlsStarting(value) {
  starting = value;
  renderModels();
}

function renderEffort() {
  const choice = selected || defaultModel;
  const model = catalogs.get(choice?.provider)?.find(m => m.id === choice?.model);
  const control = model?.reasoning_control;
  sliderLabels = control?.labels || {};
  sliderLevels = ['', ...(Array.isArray(control?.levels) ? control.levels.filter(lv => levels.includes(lv) && lv) : levels.slice(1))];
  // A saved preference may be higher than this model supports. Show the
  // effective rung while retaining the preference for the next model.
  const rank = Math.max(0, levels.indexOf(requestedEffort));
  const supported = sliderLevels.filter(lv => lv && levels.indexOf(lv) <= rank);
  const effective = !requestedEffort || sliderLevels.length === 1 ? ''
    : sliderLevels.includes(requestedEffort) ? requestedEffort : supported.at(-1) || sliderLevels[1];
  const range = $('#effort');
  range.max = String(Math.max(1, sliderLevels.length - 1));
  range.value = String(sliderLevels.indexOf(effective));
  range.disabled = sliderLevels.length === 1 || changing || starting;
  const label = sliderLabels[effective] || labels[levels.indexOf(effective)];
  $('#effort-value').textContent = label;
  range.setAttribute('aria-valuetext', label);
  $('#effort-wrap').title = control?.note || 'Auto uses the model default. Each provider uses its supported reasoning settings.';
  range.setAttribute('aria-description', $('#effort-wrap').title);
}

export function wireChatControls(callbacks) {
  hooks = callbacks;
  let saved = '';
  try { saved = localStorage.getItem('omote.effort') || ''; } catch { /* no storage */ }
  requestedEffort = levels.includes(saved) ? saved : '';
  renderEffort();
  $('#effort').oninput = () => {
    const value = sliderLevels[Number($('#effort').value)] || '';
    const label = sliderLabels[value] || labels[levels.indexOf(value)];
    $('#effort-value').textContent = label;
    $('#effort').setAttribute('aria-valuetext', label);
  };
  $('#effort').onchange = () => {
    requestedEffort = sliderLevels[Number($('#effort').value)] || '';
    hooks.onEffort(requestedEffort);
  };
  $('#chat-model').onchange = async () => {
    const raw = $('#chat-model').value;
    const pair = raw ? JSON.parse(raw) : null;
    const choice = pair ? { provider: pair[0], model: pair[1] } : null;
    const id = currentSession();
    if (!id) { rememberModel(choice); renderModels(); return; }
    if (!choice) return;
    const previous = selected;
    selected = choice;
    changing = true;
    hooks.onChanging(true);
    renderModels();
    try {
      const response = await api(`/api/sessions/${encodeURIComponent(id)}/model`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(choice),
      });
      let result = null;
      try { result = await response?.json(); } catch { /* reported below */ }
      if (!response?.ok || !result?.model) {
        if (currentSession() === id) {
          hooks.onError(result?.detail || 'Could not change the model. Try again.');
          selected = previous;
        }
      } else if (currentSession() === id) {
        rememberModel({ provider: result.provider, model: result.model });
        hooks.onModel(result);
      }
    } finally {
      changing = false;
      hooks.onChanging(false);
      renderModels();
    }
  };
  renderModels();
}
