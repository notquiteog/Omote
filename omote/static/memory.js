/* Memory remains inspectable and correctable when recall/capture are paused. */
import { $, api, el } from './dom.js';
import { showConnections } from './connections.js';

let editing = null;
let offset = 0;
let revision = 0;
let enabled = false;
let filterTimer;

function filters() {
  const params = new URLSearchParams({ limit: '100', scope: $('#mem-scope').value });
  if ($('#mem-query').value.trim()) params.set('q', $('#mem-query').value.trim());
  if ($('#mem-filter-kind').value) params.set('kind', $('#mem-filter-kind').value);
  if ($('#mem-filter-subject').value) params.set('subject', $('#mem-filter-subject').value);
  return params;
}

function choices(selector, values, placeholder) {
  const select = $(selector);
  const selected = select.value;
  select.replaceChildren();
  for (const [value, label] of [['', placeholder], ...values]) {
    const option = el('option', '', label);
    option.value = value;
    select.appendChild(option);
  }
  // Keep an active filter even if deleting its final match removed the facet.
  if (selected && !values.some(([value]) => value === selected)) {
    const option = el('option', '', selected);
    option.value = selected;
    select.appendChild(option);
  }
  select.value = selected;
}

async function request(path, options) {
  const response = await api(`/api/memory${path}`, options);
  if (!response) throw new Error('The daemon is not reachable.');
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not save this change.');
  }
  return response;
}

function report(error) {
  $('#mem-status').textContent = error.message || String(error);
}

function resetForm() {
  editing = null;
  $('#mem-form').reset();
  $('#mem-save').textContent = 'Add memory';
  $('#mem-save').disabled = !enabled;
  $('#mem-cancel').hidden = true;
  $('#mem-editor').open = false;
  $('#mem-editor > summary').textContent = 'Add a memory';
}

function row(memory) {
  const li = el('li');
  const text = el('div', 'text');
  text.appendChild(el('div', '', memory.text));
  const date = new Date(memory.created_at * 1000).toLocaleString();
  text.appendChild(el('div', 'meta', [memory.kind, memory.subject || 'About you', date,
    `source: ${memory.source || 'unspecified'}`, `recalled ${memory.hits}×`].join(' · ')));
  li.appendChild(text);
  const actions = el('div', 'memory-actions');
  const edit = el('button', 'ghost small', 'Edit');
  edit.onclick = () => {
    editing = memory.id;
    $('#mem-text').value = memory.text;
    if (![...$('#mem-kind').options].some((option) => option.value === memory.kind)) {
      const option = el('option', '', memory.kind);
      option.value = memory.kind;
      $('#mem-kind').appendChild(option);
    }
    $('#mem-kind').value = memory.kind;
    $('#mem-subject').value = memory.subject || '';
    $('#mem-save').textContent = 'Save correction';
    $('#mem-save').disabled = false;
    $('#mem-cancel').hidden = false;
    $('#mem-editor').open = true;
    $('#mem-editor > summary').textContent = 'Edit memory';
    $('#mem-text').focus();
  };
  const forget = el('button', 'ghost small forget', 'Forget');
  forget.onclick = async () => {
    forget.disabled = true;
    try {
      await request(`/${encodeURIComponent(memory.id)}`, { method: 'DELETE' });
      if (editing === memory.id) resetForm();
      await load();
    } catch (error) { report(error); }
    finally { forget.disabled = false; }
  };
  actions.append(edit, forget);
  li.appendChild(actions);
  return li;
}

async function load(more = false) {
  clearTimeout(filterTimer);
  const version = ++revision;
  const params = filters();
  params.set('offset', String(more ? offset : 0));
  $('#mem-list').setAttribute('aria-busy', 'true');
  $('#mem-more').disabled = true;
  try {
    const [settings, page] = await Promise.all([
      request('/settings').then((res) => res.json()),
      request(`?${params}`).then((res) => res.json()),
    ]);
    if (version !== revision) return;
    enabled = settings.enabled;
    $('#mem-enabled').checked = enabled;
    $('#mem-auto').checked = settings.auto_capture;
    $('#mem-auto').disabled = !enabled;
    $('#mem-save').disabled = !enabled && !editing;
    $('#mem-reembed').disabled = !enabled;
    if (!$('#mem-kind').options.length) {
      for (const kind of page.catalog.kinds) {
        const option = el('option', '', kind);
        option.value = kind;
        $('#mem-kind').appendChild(option);
      }
    }
    choices('#mem-filter-kind', page.catalog.kinds.map((kind) => [kind, kind]), 'All kinds');
    choices('#mem-filter-subject', page.catalog.subjects.map(({ name, count }) => [name, `${name} (${count})`]), 'All subjects');
    if (!more) { $('#mem-list').replaceChildren(); offset = 0; }
    $('#mem-list').append(...page.memories.map(row));
    offset += page.memories.length;
    $('#mem-more').hidden = offset >= page.matched;
    $('#mem-count').textContent = `${page.total} saved · ${page.catalog.personal} about you · ${page.catalog.projects} about projects & subjects. ` + (enabled
      ? 'Recall is on. Automatic capture is ' + (settings.auto_capture ? 'on.' : 'off.')
      : 'Memory is paused. Nothing is captured or recalled; your saved entries remain below.');
    $('#mem-results').textContent = `Showing ${offset} of ${page.matched} matching memories`;
    $('#mem-empty').hidden = page.matched !== 0;
    $('#mem-empty').textContent = page.total
      ? 'No memories match these filters. Try another search or choose Everything and All subjects.'
      : 'No memories yet. Add a detail you want Omote to remember.';
    $('#mem-provider').textContent = settings.embedding_model
      ? `Embedding model: ${settings.embedding_model}. Text is sent to your selected embedding connection for indexing and search.`
      : 'No embedding model selected. You can save memories locally; choose a connection to enable recall.';
    const index = await (await request('/index')).json();
    if (version === revision) $('#mem-provider').textContent += ` ${index.searchable} indexed · ${index.pending} pending.`;
  } catch (error) { if (version === revision) report(error); }
  finally {
    if (version === revision) {
      $('#mem-list').setAttribute('aria-busy', 'false');
      $('#mem-more').disabled = false;
    }
  }
}

function filterChanged() {
  // Invalidate outstanding pages as soon as typing starts. Old results must
  // not overwrite the new query during the debounce window.
  ++revision;
  clearTimeout(filterTimer);
  $('#mem-more').hidden = true;
  $('#mem-results').textContent = 'Searching…';
  filterTimer = setTimeout(() => load(), 180);
}

function jsonRequest(method, body) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

async function settingsChanged() {
  try {
    await request('/settings', jsonRequest('PUT', {
      enabled: $('#mem-enabled').checked, auto_capture: $('#mem-auto').checked,
    }));
    await load();
  } catch (error) { report(error); await load(); }
}

export function wireMemory() {
  $('#open-memory').onclick = () => {
    resetForm();
    $('#mem-status').textContent = '';
    load();
    $('#memory-dialog').showModal();
  };
  $('#mem-close').onclick = () => $('#memory-dialog').close();
  $('#mem-enabled').onchange = settingsChanged;
  $('#mem-auto').onchange = settingsChanged;
  $('#mem-more').onclick = () => load(true);
  $('#mem-query').oninput = filterChanged;
  $('#mem-filter-kind').onchange = filterChanged;
  $('#mem-filter-subject').onchange = filterChanged;
  $('#mem-scope').onchange = () => {
    $('#mem-filter-subject').value = '';
    $('#mem-filter-subject').disabled = $('#mem-scope').value === 'personal';
    filterChanged();
  };
  $('#mem-cancel').onclick = resetForm;
  $('#mem-connections').onclick = () => { $('#memory-dialog').close(); showConnections(); };
  $('#mem-form').onsubmit = async (event) => {
    event.preventDefault();
    $('#mem-save').disabled = true;
    try {
      const response = await request(editing ? `/${encodeURIComponent(editing)}` : '', jsonRequest(editing ? 'PUT' : 'POST', {
        text: $('#mem-text').value.trim(), kind: $('#mem-kind').value,
        subject: $('#mem-subject').value.trim() || null,
      }));
      const saved = await response.json();
      $('#mem-status').textContent = saved.indexed === false
        ? 'Memory saved locally. Recall will be available after indexing; connect an embedding model and index pending memories in Memory settings.'
        : 'Memory saved.';
      resetForm();
      await load();
    } catch (error) { report(error); }
    finally { $('#mem-save').disabled = !enabled && !editing; }
  };
  $('#mem-export').onclick = async () => {
    try {
      const blob = await (await request('/export')).blob();
      const url = URL.createObjectURL(blob);
      const anchor = el('a');
      anchor.href = url;
      anchor.download = 'omote-memories.json';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { report(error); }
  };
  $('#mem-reembed').onclick = async () => {
    $('#mem-reembed').disabled = true;
    $('#mem-status').textContent = 'Rebuilding pending embeddings…';
    try {
      const result = await (await request('/index/reembed', { method: 'POST' })).json();
      $('#mem-status').textContent = `${result.embedded} embedded · ${result.failed} failed.`;
      await load();
    } catch (error) { report(error); }
    finally { $('#mem-reembed').disabled = !enabled; }
  };
  $('#mem-wipe').onclick = async () => {
    if (!confirm('Delete every saved memory and its search vectors? This also turns memory off and cannot be undone.')) return;
    try {
      await request('', { method: 'DELETE' });
      resetForm();
      $('#mem-status').textContent = 'All memories deleted. Memory is off.';
      await load();
    } catch (error) { report(error); }
  };
  api('/api/memory/settings').then((res) => {
    if (res?.ok) $('#open-memory').hidden = false;
  });
}
