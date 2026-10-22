/* Saved work is read on demand; browsing it never sends a model a prompt. */
import { $, api, el } from './dom.js';

let revision = 0;
let searchTimer;
let rows = [];
let total = 0;
let nextOffset = 0;
let openChat;
let openStudio;
let visible = false;

function link(text, path, filename = '') {
  const node = el('a', 'task-file', text);
  // Only local result routes. Never navigate to a URL supplied in model prose.
  if (!/^\/api\/(?:media\/[A-Za-z0-9]+\/file|schedules\/results\/chats\/[A-Za-z0-9_-]+\/files\/[a-f0-9]+|sessions\/[A-Za-z0-9_-]+\/export\.html)$/.test(path)) return node;
  node.href = path; node.download = filename;
  return node;
}
function button(text, callback) {
  const node = el('button', 'small', text); node.type = 'button';
  node.onclick = async () => {
    node.disabled = true;
    try { await callback(); }
    catch (error) { $('#task-results-status').textContent = error.message; }
    finally { node.disabled = false; }
  };
  return node;
}
function paint() {
  const list = $('#task-results-list'); list.replaceChildren();
  for (const result of rows) {
    const card = el('article', 'schedule-run task-result');
    card.dataset.resultId = result.id; card.dataset.state = result.status;
    const head = el('div', 'schedule-heading');
    head.append(el('h3', '', result.title), el('span', 'pill', result.status === 'saved' ? 'saved' : result.status));
    const source = result.kind === 'media' ? 'Studio' : result.kind === 'run' ? 'Scheduled task' : 'Chat';
    card.append(head, el('p', 'meta', `${source} · ${new Date(result.created * 1000).toLocaleString()}`));
    if (result.error) card.append(el('p', 'setup-status error', result.error));
    if (result.summary) {
      const details = el('details', 'task-result-summary');
      details.append(el('summary', '', 'Read saved reply'), el('pre', 'schedule-result', result.summary));
      card.append(details);
    }
    if (result.session_id && ['interrupted', 'failed'].includes(result.status)) {
      const details = el('details', 'task-result-summary');
      const preview = el('div', 'task-chat-preview');
      details.append(el('summary', '', 'Review saved conversation'), preview);
      let loaded = false;
      details.ontoggle = async () => {
        if (!details.open || loaded) return;
        loaded = true; preview.textContent = 'Loading saved messages…';
        const res = await api(`/api/schedules/results/chats/${encodeURIComponent(result.session_id)}`);
        const data = await res?.json().catch(() => null);
        preview.replaceChildren();
        if (!res?.ok || !data) {
          loaded = false; preview.textContent = 'Could not load this conversation. Close and reopen this section to retry.'; return;
        }
        for (const message of data.messages) {
          const entry = el('div');
          entry.append(el('strong', '', message.role === 'user' ? 'You' : 'Omote'), el('pre', 'schedule-result', message.text));
          preview.append(entry);
        }
        preview.append(el('p', 'meta', data.messages.length
          ? 'Recent saved messages. Save the conversation for older messages and tool logs.'
          : 'No recent messages were saved. Save the conversation to check the full transcript.'));
      };
      card.append(details);
    }
    if (result.files?.length) {
      const files = el('div', 'task-result-files');
      files.append(el('span', 'meta', result.kind === 'media' ? 'Download' : 'Linked project files'));
      for (const file of result.files) files.append(link(file.name, file.url, file.name));
      if (result.kind !== 'media') files.append(el('span', 'meta', 'Downloads use the current version of each linked file.'));
      card.append(files);
    }
    const controls = el('div', 'schedule-actions');
    if (result.session_id) {
      controls.append(link('Save conversation', `/api/sessions/${encodeURIComponent(result.session_id)}/export.html`));
      controls.append(button(result.status === 'interrupted' ? 'Continue in chat' : 'Open chat', async () => {
        await openChat(result.session_id); $('#schedules-dialog').close();
      }));
    } else if (result.kind === 'media') {
      controls.append(button('Open Studio', () => { openStudio(); $('#schedules-dialog').close(); }));
    }
    card.append(controls); list.append(card);
  }
  if (!rows.length) {
    const empty = el('div', 'task-empty');
    empty.append(el('strong', '', $('#task-results-search').value.trim() ? 'No matching results' : 'Your saved work will appear here'),
      el('span', '', 'Find replies, scheduled reports and generated files here, even after restarting Omote.'));
    list.append(empty);
  }
}
export async function loadResults(more = false) {
  if (!visible) return;
  const requestRevision = ++revision;
  const offset = more ? nextOffset : 0;
  const params = new URLSearchParams({ q: $('#task-results-search').value.trim(), offset, limit: 20 });
  $('#task-results-status').textContent = 'Finding saved work…';
  $('#task-results-more').disabled = true;
  const res = await api(`/api/schedules/results?${params}`);
  const data = await res?.json().catch(() => null);
  if (requestRevision !== revision || !visible) return;
  if (!res?.ok || !data) {
    $('#task-results-status').textContent = 'Saved work could not be loaded. Try Refresh when Omote reconnects.';
    $('#task-results-more').disabled = false;
    return;
  }
  rows = more ? [...rows, ...data.results.filter(item => !rows.some(old => old.id === item.id))] : data.results;
  nextOffset = offset + data.results.length;
  total = data.total;
  paint();
  $('#task-results-status').textContent = `${rows.length} of ${total} saved results`;
  $('#task-results-more').hidden = !data.has_more;
  $('#task-results-more').disabled = false;
  $('#task-results-limit').hidden = !data.limited;
}
export function showResults() { visible = true; return loadResults(); }
export function hideResults() { visible = false; ++revision; clearTimeout(searchTimer); }
export function wireResults({ onOpenChat, onOpenStudio }) {
  openChat = onOpenChat; openStudio = onOpenStudio;
  $('#task-results-search').oninput = () => {
    ++revision; clearTimeout(searchTimer);
    $('#task-results-more').disabled = true;
    searchTimer = setTimeout(() => loadResults(), 250);
  };
  $('#task-results-more').onclick = () => loadResults(true);
}
