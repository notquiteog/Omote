import { $, api, el } from './dom.js';

let wired = false;
export function wireCreativeApps() {
  if (wired) return;
  wired = true;
  const open = el('button', 'ghost small', 'Creative apps'); open.type = 'button'; open.id = 'studio-creative-apps';
  $('.studio-workspace-bar').append(open);
  const dialog = el('dialog'); dialog.id = 'creative-apps-dialog'; dialog.setAttribute('aria-labelledby', 'creative-apps-title');
  dialog.innerHTML = `<header class="lm-heading"><h2 id="creative-apps-title">Creative apps</h2><button class="ghost" id="creative-apps-close" aria-label="Close creative apps">✕</button></header>
    <p class="lm-intro">Let your chat model work with real editing tools. Save an editable project, export a delivery copy, then check the result.</p>
    <div class="lm-toolbar"><label>App <select id="creative-apps-choice"></select></label><button id="creative-apps-refresh" class="ghost small">Refresh</button></div>
    <p id="creative-apps-status" class="meta" role="status" aria-live="polite"></p><article class="lm-card" id="creative-apps-detail"></article>
    <details class="lm-advanced"><summary>Adobe, ArtCraft and other app connections</summary>
      <p class="meta">Use an installed app’s supported MCP bridge. Add its server to your <code>.mcp.json</code> with <code>"creative": true</code>, then restart Omote. The chat discovers its actual tools and export formats. App licenses, authentication and bridge availability still apply.</p>
      <p class="meta">ArtCraft’s local engines use their installed CLI. On Windows the CLI may be inside Program Files; on macOS it may be a separate download. You can set <code>OMOTE_PHOTOCRAFT_BIN</code>, <code>OMOTE_FILMCRAFT_BIN</code> or the equivalent app variable to its full path.</p>
      <p class="meta">Choose a model that supports tools. Visual review also needs image input. A model name alone does not install editors or grant access.</p>
    </details>`;
  document.body.append(dialog);
  let rows = [], epoch = 0;
  function paint() {
    const row = rows.find(item => item.id === $('#creative-apps-choice').value);
    const target = $('#creative-apps-detail'); target.replaceChildren();
    if (!row) { target.append(el('p', 'meta', 'No apps found. Refresh or configure a creative MCP connection.')); return; }
    target.append(el('h3', '', row.label), el('p', 'meta', row.purpose),
      el('p', 'meta', row.available ? 'Found on this computer or connected through MCP. The chat will discover its actual tool schemas.' : 'Setup needed.'),
      el('p', 'meta', row.setup), el('p', 'meta', `File access: ${row.filesystem_scope}.`));
    const actions = el('div', 'lm-actions');
    const use = el('button', 'primary', row.available ? 'Use in chat' : 'Set up with chat'); use.type = 'button';
    use.onclick = () => {
      dialog.close();
      document.dispatchEvent(new CustomEvent('omote:creative-app', { detail: row }));
    };
    actions.append(use);
    if (row.documentation) { const link = el('a', 'ghost small', 'App documentation'); link.href = row.documentation; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link); }
    target.append(actions);
  }
  async function refresh() {
    const generation = ++epoch;
    $('#creative-apps-status').textContent = 'Checking installed engines and connected apps…';
    const response = await api('/api/creative/apps');
    if (generation !== epoch || !dialog.open) return;
    if (!response?.ok) { $('#creative-apps-status').textContent = 'Could not load creative apps. Try Refresh.'; return; }
    const data = await response.json();
    if (generation !== epoch || !dialog.open) return;
    const previous = $('#creative-apps-choice').value;
    rows = data.apps.sort((a, b) => Number(b.available) - Number(a.available));
    $('#creative-apps-choice').replaceChildren(...rows.map(row => { const option = el('option', '', `${row.label}${row.available ? ' · available' : ''}`); option.value = row.id; return option; }));
    if (rows.some(row => row.id === previous)) $('#creative-apps-choice').value = previous;
    const available = rows.filter(row => row.available).length;
    $('#creative-apps-status').textContent = `${available} available · ${data.note}`;
    paint();
  }
  open.onclick = () => { dialog.showModal(); refresh(); };
  $('#creative-apps-choice').onchange = paint;
  $('#creative-apps-close').onclick = () => dialog.close();
  $('#creative-apps-refresh').onclick = refresh;
  dialog.onclose = () => { epoch++; };
}
