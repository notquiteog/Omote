/* Optional setup and a replayable introduction. Credentials never enter browser storage. */
import { $, api, el, json } from './dom.js';
import { showConnections } from './connections.js';

const SEEN = 'omote.welcome.v1';
let hosts = [];
let ready = false;
let saving = false;
let refreshed = async () => {};
let step = 0;
const steps = [
  { title: 'Start with what you need.', visual: ['Your idea', 'A conversation', 'Work gets done'],
    description: 'Ask a question or describe a task in plain language. Your first message starts a chat automatically.',
    tips: ['Use New chat to choose a project folder or change models.', 'Attach images, use @ to mention files, or type / to explore commands.', 'Past chats stay in the sidebar. Search them anytime.'] },
  { title: 'You stay in control.', visual: ['Review the action', 'Approve or decline', 'Stop anytime'],
    description: 'New chats use Ask first. Omote can read to understand your task and asks before actions that need permission.',
    tips: ['Review tool cards to see what it is doing and why.', 'Use Stop to interrupt a running task.', 'Review changes or Rewind when you want to inspect or undo file edits.'] },
  { title: 'A studio for your ideas.', visual: ['Create', 'Characters', 'Library', 'ComfyUI'],
    description: 'Open Studio to generate images and video, reuse character references and explore your media library.',
    tips: ['Choose a connected provider and model, then describe what to create.', 'Save characters and references for your next story.', 'The graph workspace connects to your ComfyUI installation, including its supported Nodes 2.0 editor.'] },
  { title: 'Memory belongs to you.', visual: ['Choose what to remember', 'Inspect', 'Edit or delete'],
    description: 'Memory starts paused for new users. Turn it on only when you want Omote to remember details across chats.',
    tips: ['Memory is always available from the sidebar.', 'Review, edit, export or delete saved facts and project information.', 'Memory uses an embedding model. Choose where that data goes in AI providers.'] },
  { title: 'Make it your workspace.', visual: ['Voice', 'Your providers', 'Your companion'],
    description: 'You’re ready to explore. Add more capabilities whenever you need them.',
    tips: ['Talk uses your speech and chat providers. Live voice uses a separate OpenAI API connection.', 'Browser control, ComfyUI and creative tools show their setup options where you use them.', 'Change themes and providers in Settings, or choose a companion in the sidebar. Replay this tour from Getting started.'] },
];

function seen() { try { return localStorage.getItem(SEEN) === 'done'; } catch { return false; } }
function remember() { try { localStorage.setItem(SEEN, 'done'); } catch { /* optional convenience */ } }
function status(message = '', error = false) {
  $('#welcome-status').textContent = message;
  $('#welcome-status').classList.toggle('error', error);
}
function closeWelcome() {
  if (saving) return;
  remember();
  $('#welcome-dialog').close();
}
function chooseHost() {
  const host = hosts.find(h => h.id === $('#welcome-provider').value);
  if (!host) return;
  $('#welcome-key').value = '';
  $('#welcome-key').type = 'password';
  $('#welcome-show-key').textContent = 'Show';
  $('#welcome-show-key').setAttribute('aria-pressed', 'false');
  $('#welcome-show-key').setAttribute('aria-label', 'Show API key');
  $('#welcome-key-field').hidden = host.local && !host.env_key;
  $('#welcome-key').required = !host.local;
  $('#welcome-address-field').hidden = !host.local;
  $('#welcome-address').value = host.base_url;
  $('#welcome-address').required = host.local;
  $('#welcome-provider-note').textContent = host.id === 'openrouter'
    ? 'One key, many models. We’ll choose a chat model automatically; you can change it later.'
    : host.local ? 'Start your local model server first. We’ll use one of its available chat models.'
    : `Connect ${host.label} and we’ll choose an available chat model. Change models anytime in Settings.`;
  $('#welcome-key-link').hidden = host.id !== 'openrouter';
  status();
}
async function loadHosts() {
  hosts = [];
  $('#welcome-provider').replaceChildren();
  const data = await json('/api/providers/catalog');
  if (!data) throw new Error('Could not load providers. Check that Omote is running, then try again.');
  hosts = data.hosts.filter(h => h.modalities.includes('chat') && h.id !== 'perch');
  hosts.sort((a, b) => (a.id === 'openrouter' ? -1 : b.id === 'openrouter' ? 1 : a.label.localeCompare(b.label)));
  const select = $('#welcome-provider');
  select.replaceChildren(...hosts.map(h => {
    const option = el('option', '', `${h.label}${h.local ? ' · local' : ''}`);
    option.value = h.id; return option;
  }));
  chooseHost();
}
export async function openSetup() {
  if (saving) return;
  $('#welcome-connect-panel').hidden = false;
  $('#welcome-ready-panel').hidden = true;
  $('#welcome-dialog').setAttribute('aria-labelledby', 'welcome-title');
  if (!$('#welcome-dialog').open) $('#welcome-dialog').showModal();
  $('#welcome-submit').disabled = true;
  try { await loadHosts(); $('#welcome-key').focus(); }
  catch (error) { status(error.message, true); }
  finally { $('#welcome-submit').disabled = false; }
}
export function updateSetup(data) {
  ready = Boolean(data.capabilities?.chat?.length);
  $('#setup-banner').hidden = ready;
  if (!ready && !seen() && !document.querySelector('dialog[open]')) openSetup();
}
function renderStep() {
  const current = steps[step];
  $('#guide-title').textContent = current.title;
  $('#guide-description').textContent = current.description;
  $('#guide-progress').textContent = `QUICK TOUR · ${step + 1} OF ${steps.length}`;
  $('#guide-visual').replaceChildren(...current.visual.map(text => el('span', '', text)));
  $('#guide-tips').replaceChildren(...current.tips.map(text => el('li', '', text)));
  $('#guide-dots').replaceChildren(...steps.map((_, n) => el('span', n === step ? 'current' : '')));
  $('#guide-back').disabled = step === 0;
  $('#guide-next').textContent = step === steps.length - 1 ? 'Let’s begin' : 'Next';
  $('#guide-title').focus();
}
function openTour() {
  closeWelcome();
  step = 0;
  $('#guide-dialog').showModal();
  renderStep();
}
export function wireOnboarding(onConnected) {
  refreshed = onConnected;
  $('#setup-connect').onclick = openSetup;
  $('#open-guide').onclick = () => ready ? openTour() : openSetup();
  $('#settings-quick-connect').onclick = () => { $('#settings-dialog').close(); openSetup(); };
  $('#welcome-provider').onchange = chooseHost;
  $('#welcome-close').onclick = closeWelcome;
  $('#welcome-skip').onclick = closeWelcome;
  $('#welcome-preview-tour').onclick = openTour;
  $('#welcome-start').onclick = () => { closeWelcome(); $('#input').focus(); };
  $('#welcome-tour').onclick = openTour;
  $('#welcome-advanced').onclick = () => { closeWelcome(); showConnections(); $('#connection-advanced').open = true; };
  $('#welcome-dialog').addEventListener('cancel', event => {
    if (saving) event.preventDefault(); else remember();
  });
  $('#welcome-dialog').addEventListener('close', () => {
    $('#welcome-key').value = '';
    $('#welcome-key').type = 'password';
  });
  $('#welcome-show-key').onclick = () => {
    const reveal = $('#welcome-key').type === 'password';
    $('#welcome-key').type = reveal ? 'text' : 'password';
    $('#welcome-show-key').textContent = reveal ? 'Hide' : 'Show';
    $('#welcome-show-key').setAttribute('aria-pressed', String(reveal));
    $('#welcome-show-key').setAttribute('aria-label', reveal ? 'Hide API key' : 'Show API key');
  };
  $('#welcome-form').onsubmit = async event => {
    event.preventDefault();
    if (saving) return;
    const host = hosts.find(h => h.id === $('#welcome-provider').value);
    if (!host) { status('Choose a provider to continue.', true); return; }
    saving = true;
    for (const control of $('#welcome-dialog').querySelectorAll('button, input, select')) control.disabled = true;
    $('#welcome-submit').textContent = 'Checking connection…';
    status('Verifying your connection and finding a chat model…');
    try {
      const res = await api('/api/providers/connections', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host_id: host.id, api_key: $('#welcome-key').value.trim() || undefined,
          ...(host.local ? { base_url: $('#welcome-address').value.trim() } : {}),
          verify: true, configure_chat: true }),
      });
      if (!res) throw new Error('Omote is not reachable. Keep this window open and try again.');
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not connect. Check your key and try again.');
      $('#welcome-key').value = '';
      remember();
      await refreshed();
      $('#welcome-connect-panel').hidden = true;
      $('#welcome-ready-panel').hidden = false;
      $('#welcome-ready-panel h2').id = 'welcome-ready-title';
      $('#welcome-dialog').setAttribute('aria-labelledby', 'welcome-ready-title');
      $('#welcome-ready-description').textContent = `${host.label} is connected. Chat will use ${result.chat_model || 'your default model'}. You can change this in Settings → AI providers.`;
    } catch (error) { status(error.message, true); }
    finally {
      saving = false;
      for (const control of $('#welcome-dialog').querySelectorAll('button, input, select')) control.disabled = false;
      $('#welcome-submit').textContent = 'Connect and continue';
      if (!$('#welcome-ready-panel').hidden) $('#welcome-start').focus();
    }
  };
  $('#guide-back').onclick = () => { if (step > 0) { step--; renderStep(); } };
  $('#guide-next').onclick = () => {
    if (step < steps.length - 1) { step++; renderStep(); }
    else { $('#guide-dialog').close(); if (ready) $('#input').focus(); else $('#setup-connect').focus(); }
  };
  $('#guide-close').onclick = () => $('#guide-dialog').close();
}
