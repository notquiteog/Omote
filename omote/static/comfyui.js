/* Use the upstream editor, including its Nodes 2.0 renderer and custom nodes.
 * A frame is kept mounted across mode switches so unsaved graphs survive. */
import { $, api, el } from './dom.js';
import { showConnections } from './connections.js';
import { openToolSetup } from './tool-setup.js';

let editors = [];
let activeURL = '';
let activeID = '';
let refreshEpoch = 0;
let loadingTimer = null;

function focusMode(on) {
  $('#pane-studio').classList.toggle('comfy-focus', on);
  $('#comfy-focus').setAttribute('aria-pressed', String(on));
  $('#comfy-focus').textContent = on ? 'Exit focus' : 'Focus mode';
}

function selected() {
  return editors.find((entry) => entry.id === $('#comfy-provider').value);
}

function describe() {
  const entry = selected();
  $('#comfy-connect').disabled = !entry?.available;
  $('#comfy-reload').disabled = !activeURL;
  const link = $('#comfy-external');
  link.hidden = !entry?.available;
  if (entry?.available) link.href = entry.url;
  else link.removeAttribute('href');
  $('#comfy-status').textContent = entry
    ? (entry.reason || 'Open your ComfyUI editor to edit graphs and run workflows. Sign in to ComfyUI there if required.')
    : 'Set up a local studio to use the full ComfyUI editor. Omote handles the installation and connection.';
  $('#comfy-empty-action').textContent = entry?.available ? 'Open ComfyUI editor' : 'Set up local studio';
}

async function refresh() {
  const epoch = ++refreshEpoch;
  const res = await api('/api/media/comfyui/editors');
  if (epoch !== refreshEpoch) return;
  if (!res?.ok) {
    $('#comfy-status').textContent = 'Could not load ComfyUI connections. Try Refresh connections.';
    return;
  }
  const previous = $('#comfy-provider').value;
  const data = await res.json();
  if (epoch !== refreshEpoch) return;
  editors = data.editors;
  $('#comfy-provider').replaceChildren(...editors.map((entry) => {
    const option = el('option', '', entry.label);
    option.value = entry.id;
    return option;
  }));
  if (editors.some((entry) => entry.id === previous)) $('#comfy-provider').value = previous;
  // Removing a connection or enabling local-only must detach the old editor.
  if (activeURL && !editors.some((entry) => entry.available && entry.url === activeURL)) {
    $('#comfy-frame').removeAttribute('src');
    $('#comfy-frame').hidden = true;
    activeURL = '';
    activeID = '';
    clearTimeout(loadingTimer);
    $('#comfy-editor-stage').classList.remove('editor-loading');
  }
  describe();
}

function openEditor() {
  const entry = selected();
  if (!entry?.available) return;
  const url = new URL(entry.url);
  if (url.origin === location.origin) {
    $('#comfy-status').textContent = 'Use a separate ComfyUI origin, not the Omote address.';
    return;
  }
  if (location.protocol === 'https:' && url.protocol === 'http:') {
    $('#comfy-status').textContent = 'This HTTPS page cannot embed an HTTP editor. Use Open in tab or give ComfyUI an HTTPS address.';
    return;
  }
  if (activeURL === entry.url) {
    activeID = entry.id;
    $('#comfy-frame').hidden = false;
    $('#comfy-status').textContent = 'ComfyUI is already open. Use Focus mode for more canvas space.';
    return;
  }
  if (activeURL && activeURL !== entry.url && !confirm('Switch editors? Save your current ComfyUI graph first.')) {
    $('#comfy-provider').value = activeID;
    return;
  }
  if (activeURL !== entry.url) {
    $('#comfy-editor-stage').classList.add('editor-loading');
    $('#comfy-frame').src = entry.url;
    clearTimeout(loadingTimer);
    loadingTimer = setTimeout(() => {
      $('#comfy-editor-stage').classList.remove('editor-loading');
      $('#comfy-status').textContent = 'The editor is taking longer to open. Check that ComfyUI is running, or use Open in tab if embedding is blocked.';
    }, 12000);
  }
  activeURL = entry.url;
  activeID = entry.id;
  $('#comfy-reload').disabled = false;
  $('#comfy-frame').hidden = false;
  $('#comfy-status').textContent = 'Opening ComfyUI… Your native tools, nodes and workflow tabs are available inside the editor.';
}

export function wireComfyUI() {
  const setup = el('button', 'ghost small studio-setup-button', 'Set up local studio');
  setup.type = 'button'; setup.id = 'studio-setup';
  setup.onclick = () => openToolSetup('comfyui');
  $('.studio-workspace-bar').append(setup);
  for (const [id, advanced] of [['studio-simple', false], ['studio-graph', true], ['studio-library', false]]) {
    $(`#${id}`).onclick = () => {
      const library = id === 'studio-library';
      $('#studio-generate').hidden = advanced || library;
      const libraryPane = $('#creative-workspace');
      if (libraryPane) libraryPane.hidden = !library;
      $('#comfy-workspace').hidden = !advanced;
      if (!advanced) focusMode(false);
      $('#studio-workspace-hint').textContent = advanced ? 'Your native ComfyUI workspace' : library ? 'Characters, references & finished work' : 'Describe it. Make it. Refine it.';
      for (const [button, on] of [['studio-simple', !advanced && !library], ['studio-graph', advanced], ['studio-library', library]]) {
        $(`#${button}`).classList.toggle('on', on);
        $(`#${button}`).setAttribute('aria-pressed', String(on));
      }
      if (advanced) refresh();
    };
  }
  $('#comfy-provider').onchange = () => {
    if (activeURL && selected()?.available) openEditor();
    // Keep the workbench's selected backend aligned with the visible graph.
    else if (activeURL) $('#comfy-provider').value = activeID;
    describe();
  };
  $('#comfy-connect').onclick = openEditor;
  $('#comfy-refresh').onclick = refresh;
  $('#comfy-settings').onclick = showConnections;
  $('#comfy-focus').onclick = () => focusMode(!$('#pane-studio').classList.contains('comfy-focus'));
  $('#comfy-empty-action').onclick = () => selected()?.available ? openEditor() : openToolSetup('comfyui');
  document.addEventListener('omote:open-local-studio', async () => {
    $('#start-studio')?.click();
    $('#studio-graph').click();
    await refresh();
    $('#comfy-provider').value = 'omote-managed-comfyui';
    describe(); openEditor();
  });
  $('#comfy-reload').onclick = () => {
    if (!activeURL || !confirm('Reload ComfyUI? Save your graph in the editor first; unsaved editor changes may be lost.')) return;
    const frame = $('#comfy-frame');
    frame.src = activeURL;
    $('#comfy-status').textContent = 'Reloading ComfyUI…';
  };
  $('#comfy-frame').addEventListener('load', () => {
    clearTimeout(loadingTimer);
    $('#comfy-editor-stage').classList.remove('editor-loading');
    if (activeURL) $('#comfy-status').textContent = 'Editor frame loaded. If the canvas is blank, use Open in tab; some servers block embedding.';
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !document.querySelector('dialog[open]') && $('#pane-studio').classList.contains('comfy-focus')) {
      focusMode(false); $('#comfy-focus').focus();
    }
  });
  document.addEventListener('omote:providers-changed', () => {
    if (!$('#comfy-workspace').hidden) refresh();
  });
}
