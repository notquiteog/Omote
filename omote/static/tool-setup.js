/* One goal, one button. Detailed package logs stay out of the normal flow. */
import { $, api, el } from './dom.js';

let dialog, target = 'comfyui', timer, epoch = 0, pending = false, inFlight = false, lastState = '', failure = '', latest;
const names = { comfyui: 'Local studio', playwright: 'Browser automation' };

async function request(path = '', body) {
  const res = await api('/api/setup' + path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res?.json();
  if (!res?.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not reach setup. Try again.');
  return data;
}

function active() { return dialog?.open && !document.hidden; }
async function refresh() {
  clearTimeout(timer);
  if (!active() || inFlight || pending) return;
  const version = epoch;
  inFlight = true;
  try {
    const data = await request();
    if (version !== epoch || !active()) return;
    paint(data[target]);
  } catch (error) {
    if (version === epoch) $('#ts-status').textContent = error.message;
  } finally {
    inFlight = false;
    if (active()) timer = setTimeout(refresh, 1500);
  }
}

function paint(row) {
  latest = row;
  if (!lastState) {
    $('#ts-engine').value = row.engine || 'chromium';
    $('#ts-device').value = row.preference || 'auto';
  }
  const changedBrowser = target === 'playwright' && row.state === 'ready' && $('#ts-engine').value !== (row.engine || 'chromium');
  const ready = row.state === 'ready' && !changedBrowser;
  const busy = row.busy;
  $('#ts-status').textContent = failure || row.error || row.note || (target === 'comfyui'
    ? 'Ready to set up your local creative workspace.' : 'Ready to set up a browser for web tasks.');
  if (changedBrowser && !failure) $('#ts-status').textContent = 'Set up your selected browser to use it for web tasks.';
  const progress = $('#ts-progress'); progress.hidden = !busy;
  if (row.download && row.download.total) { progress.max = row.download.total; progress.value = row.download.bytes; }
  else progress.removeAttribute('value');
  $('#ts-start').hidden = ready;
  $('#ts-start').disabled = pending || busy || row.waiting;
  $('#ts-start').textContent = busy ? 'Setting up…' : ['failed', 'interrupted', 'cancelled'].includes(row.state)
    ? 'Retry setup' : row.state === 'stopped' ? 'Start studio' : target === 'comfyui' ? 'Set up local studio' : 'Set up browser';
  $('#ts-cancel').hidden = !busy;
  $('#ts-stop').hidden = target !== 'comfyui' || !ready;
  $('#ts-models').hidden = target !== 'comfyui' || !ready;
  $('#ts-open').hidden = !ready;
  $('#ts-open').textContent = target === 'comfyui' ? 'Open studio' : 'Done';
  $('#ts-options').disabled = busy || pending || (target === 'comfyui' && ready);
  $('#ts-repair').hidden = target !== 'playwright' || !ready;
  $('#ts-repair').disabled = busy || pending;
  $('#ts-log').textContent = row.log || 'Nothing to report.';
  $('#ts-footnote').textContent = ready
    ? target === 'comfyui'
      ? 'Your studio starts with Omote. Choose models before your first creation; the editor may show missing-model messages until they are downloaded.'
      : 'Ready. Omote can now browse websites and work with pages for you.'
    : 'Setup downloads software and may take a while. You can close this window and keep using Omote.';
  $('#ts-queue').hidden = !row.waiting;
  if (ready && lastState !== 'ready') {
    document.dispatchEvent(new Event(target === 'comfyui' ? 'omote:providers-changed' : 'omote:browser-ready'));
  }
  lastState = row.state;
}

async function action(name) {
  if (pending) return;
  pending = true; failure = ''; epoch++; clearTimeout(timer);
  $('#ts-start').disabled = true; $('#ts-cancel').disabled = true; $('#ts-stop').disabled = true;
  $('#ts-status').textContent = name === 'cancel' ? 'Pausing setup…' : name === 'stop' ? 'Stopping studio…' : 'Getting things ready…';
  try {
    await request(`/${target}/${name}`, { engine: $('#ts-engine').value, device: $('#ts-device').value });
  } catch (error) { failure = error.message; $('#ts-status').textContent = failure; }
  finally {
    pending = false;
    $('#ts-cancel').disabled = false; $('#ts-stop').disabled = false;
    refresh();
  }
}

export function openToolSetup(name = 'comfyui') {
  if (!dialog) {
    dialog = el('dialog', 'tool-setup-dialog'); dialog.id = 'tool-setup-dialog';
    dialog.setAttribute('aria-labelledby', 'ts-title');
    dialog.innerHTML = `
      <header><h2 id="ts-title"></h2><button id="ts-close" class="ghost" type="button">Close</button></header>
      <p id="ts-intro" class="ts-intro"></p>
      <section class="ts-card">
        <p id="ts-status" role="status" aria-live="polite">Checking this computer…</p>
        <progress id="ts-progress" aria-label="Setup progress" hidden></progress>
        <p id="ts-queue" class="meta" hidden>Another setup is running. This will be available when it finishes.</p>
        <div class="ts-actions">
          <button id="ts-start" class="primary" type="button">Set up</button>
          <button id="ts-open" class="primary" type="button" hidden>Open studio</button>
          <button id="ts-models" class="ghost" type="button" hidden>Choose models</button>
          <button id="ts-cancel" class="ghost" type="button" hidden>Cancel setup</button>
          <button id="ts-stop" class="ghost" type="button" hidden>Stop studio</button>
        </div>
      </section>
      <p id="ts-footnote" class="meta"></p>
      <details><summary>Advanced</summary>
        <fieldset id="ts-options">
          <label id="ts-engine-label">Browser<select id="ts-engine"><option value="chromium">Chromium · recommended</option><option value="firefox">Firefox</option><option value="webkit">WebKit</option></select></label>
          <label id="ts-device-label">Processing<select id="ts-device"><option value="auto">Automatic</option><option value="cpu">CPU</option></select></label>
        </fieldset>
        <button id="ts-repair" class="ghost small" type="button" hidden>Repair browser setup</button>
        <pre id="ts-log"></pre>
      </details>`;
    document.body.append(dialog);
    $('#ts-close').onclick = () => dialog.close();
    $('#ts-start').onclick = () => action('start');
    $('#ts-cancel').onclick = () => action('cancel');
    $('#ts-stop').onclick = () => action('stop');
    $('#ts-models').onclick = () => { dialog.close(); $('#studio-assets')?.click(); };
    $('#ts-repair').onclick = () => action('start');
    $('#ts-engine').onchange = () => { if (latest) paint(latest); };
    $('#ts-open').onclick = () => {
      const selected = target; dialog.close();
      if (selected === 'comfyui') document.dispatchEvent(new Event('omote:open-local-studio'));
      else document.dispatchEvent(new Event('omote:browser-ready'));
    };
    dialog.addEventListener('close', () => { epoch++; clearTimeout(timer); });
    document.addEventListener('visibilitychange', () => {
      epoch++; clearTimeout(timer); if (active()) refresh();
    });
  }
  target = name; epoch++; lastState = ''; failure = ''; latest = null;
  $('#ts-title').textContent = names[name];
  $('#ts-intro').textContent = name === 'comfyui'
    ? 'Create with ComfyUI on this computer. Omote installs the engine, connects your model library, and opens the full editor.'
    : 'Let Omote browse websites and work with pages for you. We’ll install the browser and check that it works.';
  $('#ts-engine-label').hidden = name !== 'playwright';
  $('#ts-device-label').hidden = name !== 'comfyui';
  $('#ts-status').textContent = 'Checking this computer…';
  $('#ts-start').disabled = true;
  $('#ts-open').hidden = true; $('#ts-cancel').hidden = true; $('#ts-stop').hidden = true; $('#ts-models').hidden = true;
  dialog.showModal();
  refresh();
}
