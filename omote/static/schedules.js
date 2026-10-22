/* Scheduled work has a saved prompt and a separate, inspectable chat per run. */
import { $, api, el } from './dom.js';

let jobs = [];
let runs = [];
let activity = [];
let filter = 'all';
let editing = null;
let workspace = '';
let timer = null;
let openChat;
let openStudio;
let painted = null;
let fetching = 0;
let opened = 0;
const active = new Set(['starting', 'running', 'waiting']);
const attention = new Set(['waiting', 'failed', 'interrupted']);
const stamp = value => value ? new Date(value * 1000).toLocaleString() : '—';
const templates = {
  brief: { name: 'Morning briefing', prompt: 'Prepare a short morning briefing from my connected calendar, email and current project notes. Highlight what needs my attention, upcoming commitments and three useful next steps. Link to the sources. Tell me which sources are unavailable. Leave any replies as drafts for my review.' },
  project: { name: 'Project check-in', prompt: 'Review this project and summarize progress, unresolved issues and the next useful steps. Use the actual project files and recent changes as evidence. Do not edit files or make commits.' },
  research: { name: 'Research update', prompt: 'Research updates about [the topic I want to follow]. Compare current primary sources, explain what changed and why it matters, and include links and dates. Say when sources are unavailable or there is nothing new.' },
};

function category(item) {
  return attention.has(item.status) ? 'attention' : active.has(item.status) ? 'working' : 'finished';
}

function status(text = '', error = false) {
  $('#schedule-status').textContent = text;
  $('#schedule-status').classList.toggle('error', error);
}
async function request(path, method = 'GET', body) {
  const response = await api(`/api/schedules${path}`, { method,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  if (!response) throw new Error('Omote is not reachable. Try again when it reconnects.');
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the task fields and try again.');
  return data;
}
function cadence(spec) {
  if (spec.cadence === 'manual') return 'On demand';
  if (spec.cadence === 'interval') return `Every ${spec.interval_minutes} minutes`;
  if (spec.cadence === 'once') return `Once · ${new Date(spec.start_at).toLocaleString()}`;
  const days = spec.cadence === 'weekly' ? spec.weekdays.map(n => ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][n]).join(', ') : 'Daily';
  return `${days} · ${spec.at} · ${spec.timezone}`;
}
function action(text, callback, cls = '') {
  const button = el('button', `small ${cls}`, text);
  button.type = 'button';
  button.onclick = async () => {
    button.disabled = true;
    try { await callback(); }
    catch (error) { status(error.message, true); }
    finally { button.disabled = false; }
  };
  return button;
}
function paint() {
  const focused = document.activeElement;
  const owner = focused?.closest('[data-schedule-key]');
  const focusKey = owner?.dataset.scheduleKey;
  const focusText = focused?.textContent;
  const list = $('#schedule-list');
  list.replaceChildren();
  if (!jobs.length) list.append(el('p', 'schedule-empty', 'No scheduled tasks yet. Save a briefing, project check or recurring research task.'));
  for (const job of jobs) {
    const card = el('article', 'schedule-card');
    card.dataset.scheduleKey = job.id; card.tabIndex = -1;
    const row = el('div', 'schedule-heading');
    const title = el('h3', '', job.spec.name);
    const run = runs.find(r => r.schedule_id === job.id);
    row.append(title, el('span', 'pill', run && active.has(run.status) ? run.status : !job.spec.enabled ? 'paused' : job.spec.cadence === 'manual' ? 'on demand' : job.spec.cadence === 'once' && !job.next_run ? 'finished' : 'active'));
    card.append(row, el('p', 'meta', cadence(job.spec)), el('p', 'schedule-prompt', job.spec.prompt));
    card.append(el('p', 'meta', `Next: ${job.next_run ? stamp(job.next_run) : 'Run manually'} · ${job.spec.mode.replaceAll('_', ' ')} · ${job.spec.root}`));
    const controls = el('div', 'schedule-actions');
    const runButton = action('Run now', async () => { await request(`/${job.id}/run`, 'POST'); status('Run started. Open its chat to follow progress or answer questions.'); await refresh(); });
    runButton.disabled = Boolean(run && active.has(run.status));
    controls.append(runButton, action('Edit', () => edit(job)), action(job.spec.enabled ? 'Pause' : 'Resume', async () => {
      await request(`/${job.id}`, 'PUT', { ...job.spec, enabled: !job.spec.enabled }); await refresh();
    }), action('Delete', async () => {
      if (!confirm(`Delete “${job.spec.name}” and its run history? Its chats remain in conversation history.`)) return;
      await request(`/${job.id}`, 'DELETE'); if (editing === job.id) cancelEdit(); await refresh();
    }));
    card.append(controls);
    const history = el('details', 'schedule-history');
    history.append(el('summary', '', 'Run history'));
    const entries = el('div');
    const load = async () => {
      try {
        const data = await request(`/${job.id}/runs?limit=100`);
        entries.replaceChildren();
        if (!data.runs.length) entries.append(el('p', 'meta', 'This task has not run yet.'));
        for (const item of data.runs) entries.append(runCard(item));
      } catch (error) { entries.textContent = error.message; }
    };
    history.ontoggle = () => { if (history.open) load(); };
    history.append(entries); card.append(history); list.append(card);
  }
  paintActivity();
  if (focusKey) {
    const card = [...document.querySelectorAll('[data-schedule-key]')].find(item => item.dataset.scheduleKey === focusKey);
    const target = [...(card?.querySelectorAll('button') || [])].find(button => button.textContent === focusText && !button.disabled);
    (target || card)?.focus({ preventScroll: true });
  }
}
function paintActivity() {
  const items = [...runs.map(run => ({ ...run, kind: 'run' })), ...activity];
  const rank = { attention: 0, working: 1, finished: 2 };
  items.sort((a, b) => rank[category(a)] - rank[category(b)] || (b.started || 0) - (a.started || 0));
  for (const button of document.querySelectorAll('[data-task-filter]')) {
    const key = button.dataset.taskFilter;
    button.setAttribute('aria-pressed', String(filter === key));
    $(`#task-count-${key}`).textContent = key === 'all' ? items.length : items.filter(item => category(item) === key).length;
  }
  const shown = items.filter(item => filter === 'all' || category(item) === filter);
  const list = $('#schedule-runs');
  list.replaceChildren(...shown.map(item => item.kind === 'run' ? runCard(item) : activityCard(item)));
  if (!shown.length) {
    const empty = el('div', 'task-empty');
    empty.append(el('strong', '', filter === 'attention' ? 'Nothing needs your attention' : filter === 'working' ? 'Nothing running right now' : 'Your work will appear here'),
      el('span', '', 'Chat tasks, Studio creations and scheduled runs stay easy to find. Each chat keeps its own conversation.'));
    list.append(empty);
  }
}
function activityCard(item) {
  const card = el('article', 'schedule-run');
  card.dataset.scheduleKey = item.id; card.dataset.state = item.status; card.tabIndex = -1;
  const heading = el('div', 'schedule-heading');
  heading.append(el('h3', '', item.title), el('span', 'pill', item.status === 'waiting' ? 'Needs your input' : item.status));
  card.append(heading, el('p', 'meta', item.kind === 'chat' ? 'Chat' : 'Studio'));
  if (item.note) card.append(el('p', 'meta', item.note));
  if (item.error) card.append(el('p', 'setup-status error', item.error));
  if (typeof item.progress === 'number' && active.has(item.status)) {
    const progress = el('progress'); progress.max = 1; progress.value = Math.max(0, Math.min(1, item.progress));
    progress.setAttribute('aria-label', 'Generation progress'); card.append(progress);
  }
  const buttons = el('div', 'schedule-actions');
  if (item.session_id) buttons.append(action(item.status === 'waiting' ? 'Review request' : 'Open chat', async () => {
    await openChat(item.session_id); $('#schedules-dialog').close();
  }));
  else buttons.append(action('Open Studio', () => { openStudio(); $('#schedules-dialog').close(); }));
  card.append(buttons);
  return card;
}
function runCard(run) {
  const item = el('article', 'schedule-run');
  item.dataset.scheduleKey = run.id; item.dataset.state = run.status; item.tabIndex = -1;
  item.append(el('strong', '', `${run.snapshot.name} · ${run.status === 'waiting' ? 'Needs your input' : run.status}`),
    el('p', 'meta', stamp(run.started)));
  if (run.error) item.append(el('p', 'setup-status error', run.error));
  if (run.summary) {
    const details = el('details'); details.append(el('summary', '', 'Read result'), el('pre', 'schedule-result', run.summary)); item.append(details);
  }
  const buttons = el('div', 'schedule-actions');
  if (run.session_id) buttons.append(action('Open chat', async () => {
    await openChat(run.session_id); $('#schedules-dialog').close();
  }));
  if (active.has(run.status)) buttons.append(action('Stop run', async () => { await request(`/runs/${run.id}/stop`, 'POST'); await refresh(); }, 'danger'));
  item.append(buttons);
  return item;
}
async function refresh() {
  const revision = ++fetching;
  const data = await request('');
  if (revision !== fetching) return;
  jobs = data.schedules; runs = data.runs; workspace = data.workspace;
  activity = data.activity || [];
  $('#schedule-new').disabled = !workspace;
  for (const button of document.querySelectorAll('[data-task-template]')) button.disabled = !workspace;
  const snapshot = JSON.stringify([jobs, runs, activity]);
  if ($('#schedules-dialog').open && snapshot !== painted) { paint(); painted = snapshot; }
}
function fields() {
  const kind = $('#schedule-cadence').value;
  $('#schedule-interval-field').hidden = kind !== 'interval';
  $('#schedule-time-field').hidden = !['daily', 'weekly'].includes(kind);
  $('#schedule-days').hidden = kind !== 'weekly';
  $('#schedule-once-field').hidden = kind !== 'once';
  $('#schedule-once').required = kind === 'once';
  $('#schedule-zone-field').hidden = !['daily', 'weekly'].includes(kind);
}
function edit(job = null, template = null) {
  editing = job?.id || null;
  const spec = job?.spec || { name: '', prompt: '', root: workspace, cadence: 'daily', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    at: '09:00', weekdays: [0,1,2,3,4], interval_minutes: 60, mode: 'ask', max_runtime_minutes: 60, provider: '', model: '', enabled: true };
  if (template && !job) Object.assign(spec, templates[template]);
  const map = { name:'name', prompt:'prompt', root:'root', cadence:'cadence', timezone:'timezone', time:'at', interval:'interval_minutes', mode:'mode', limit:'max_runtime_minutes', provider:'provider', model:'model' };
  for (const [id, key] of Object.entries(map)) $(`#schedule-${id}`).value = spec[key];
  $('#schedule-enabled').checked = spec.enabled;
  $('#schedule-tools').value = (spec.tools || []).join(', ');
  for (const input of $('#schedule-days').querySelectorAll('input')) input.checked = spec.weekdays.includes(Number(input.value));
  // datetime-local is displayed in the browser's zone; its offset is included on save.
  const once = spec.start_at ? new Date(spec.start_at) : new Date(Date.now() + 3600000);
  $('#schedule-once').value = new Date(once.getTime() - once.getTimezoneOffset() * 60000).toISOString().slice(0,16);
  $('#schedule-form-title').textContent = job ? 'Edit task' : 'New scheduled task';
  $('#schedule-form').hidden = false; $('#task-overview').hidden = true;
  $('#schedule-new').hidden = true; fields(); $('#schedule-name').focus();
}
function cancelEdit() { editing = null; $('#schedule-form').hidden = true; $('#task-overview').hidden = false; $('#schedule-new').hidden = false; }
export function wireSchedules({ onOpenChat, onOpenStudio }) {
  openChat = onOpenChat;
  openStudio = onOpenStudio;
  $('#open-schedules').onclick = async () => {
    const opening = ++opened;
    $('#schedules-dialog').showModal(); status('Checking your tasks…');
    try { await refresh(); if (opening === opened) status(); } catch (error) { status(error.message, true); }
    if (opening !== opened || !$('#schedules-dialog').open) return;
    clearInterval(timer);
    timer = setInterval(async () => {
      // Do not collapse result details, steal focus, or discard edits while reading.
      if (document.hidden || !$('#schedule-form').hidden || $('#task-overview details[open]')) return;
      try { await refresh(); } catch { /* explicit Refresh retries and reports */ }
    }, 5000);
  };
  // Closing the panel preserves an unsaved draft; Cancel explicitly discards it.
  $('#schedules-dialog').addEventListener('close', () => { ++opened; clearInterval(timer); });
  $('#schedules-close').onclick = () => $('#schedules-dialog').close();
  $('#schedule-new').onclick = () => edit();
  for (const button of document.querySelectorAll('[data-task-template]')) button.onclick = () => edit(null, button.dataset.taskTemplate);
  for (const button of document.querySelectorAll('[data-task-filter]')) button.onclick = () => { filter = button.dataset.taskFilter; paintActivity(); };
  $('#schedule-refresh').onclick = async () => { try { await refresh(); status(); } catch (error) { status(error.message, true); } };
  $('#schedule-cancel').onclick = cancelEdit;
  $('#schedule-cadence').onchange = fields;
  $('#schedule-form').addEventListener('invalid', event => {
    const details = event.target.closest('details'); if (details) details.open = true;
  }, true);
  $('#schedule-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('#schedule-save'); if (button.disabled) return;
    const weekdays = [...$('#schedule-days').querySelectorAll('input:checked')].map(i => Number(i.value));
    if ($('#schedule-cadence').value === 'weekly' && !weekdays.length) { status('Choose at least one weekday.', true); return; }
    const spec = { name: $('#schedule-name').value, prompt: $('#schedule-prompt').value, root: $('#schedule-root').value,
      cadence: $('#schedule-cadence').value, timezone: $('#schedule-timezone').value, at: $('#schedule-time').value,
      interval_minutes: Number($('#schedule-interval').value), weekdays: weekdays.length ? weekdays : [0],
      mode: $('#schedule-mode').value, max_runtime_minutes: Number($('#schedule-limit').value),
      provider: $('#schedule-provider').value, model: $('#schedule-model').value, tools: [...new Set($('#schedule-tools').value.split(',').map(name => name.trim()).filter(Boolean))], enabled: $('#schedule-enabled').checked,
      start_at: $('#schedule-cadence').value === 'once' ? new Date($('#schedule-once').value).toISOString() : null };
    button.disabled = true;
    try {
      await request(editing ? `/${editing}` : '', editing ? 'PUT' : 'POST', spec);
      cancelEdit(); await refresh();
      status(!spec.enabled ? 'Task saved and paused.' : spec.cadence === 'manual'
        ? 'Task saved. Choose Run now when you’re ready.'
        : 'Task saved. It will run while Omote is running on this computer.');
    }
    catch (error) { status(error.message, true); }
    finally { button.disabled = false; }
  };
}
