/* Use the upstream editor, including its Nodes 2.0 renderer and custom nodes.
 * A frame is kept mounted across mode switches so unsaved graphs survive. */
import { $, api, el } from './dom.js';
import { showConnections } from './connections.js';

let editors = [];
let activeURL = '';

function selected() {
  return editors.find((entry) => entry.id === $('#comfy-provider').value);
}

function describe() {
  const entry = selected();
  $('#comfy-connect').disabled = !entry?.available;
  const link = $('#comfy-external');
  link.hidden = !entry?.available;
  if (entry?.available) link.href = entry.url;
  else link.removeAttribute('href');
  $('#comfy-status').textContent = entry
    ? (entry.reason || 'Open your ComfyUI editor to edit graphs and run workflows. Sign in to ComfyUI there if required.')
    : 'Connect ComfyUI in Settings → Connections to use the full graph editor. You can still generate with other providers.';
}

async function refresh() {
  const res = await api('/api/media/comfyui/editors');
  if (!res?.ok) {
    $('#comfy-status').textContent = 'Could not load ComfyUI connections. Try Refresh connections.';
    return;
  }
  const previous = $('#comfy-provider').value;
  editors = (await res.json()).editors;
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
  if (activeURL && activeURL !== entry.url && !confirm('Switch editors? Save your current ComfyUI graph first.')) return;
  if (activeURL !== entry.url) $('#comfy-frame').src = entry.url;
  activeURL = entry.url;
  $('#comfy-frame').hidden = false;
  $('#comfy-status').textContent = 'ComfyUI editor opened below. If it stays blank or refuses to connect, use Open in tab.';
}

export function wireComfyUI() {
  for (const [id, advanced] of [['studio-simple', false], ['studio-graph', true]]) {
    $(`#${id}`).onclick = () => {
      $('#studio-generate').hidden = advanced;
      $('#comfy-workspace').hidden = !advanced;
      for (const [button, on] of [['studio-simple', !advanced], ['studio-graph', advanced]]) {
        $(`#${button}`).classList.toggle('on', on);
        $(`#${button}`).setAttribute('aria-pressed', String(on));
      }
      if (advanced) refresh();
    };
  }
  $('#comfy-provider').onchange = describe;
  $('#comfy-connect').onclick = openEditor;
  $('#comfy-refresh').onclick = refresh;
  $('#comfy-settings').onclick = showConnections;
}
