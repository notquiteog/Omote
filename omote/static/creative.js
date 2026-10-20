import { $, api, el } from './dom.js';
import { applyCharacter } from './studio.js';

const prefix = '/api/creative';
let characters = [];
let assets = [];
let selected = '';
let characterTotal = 0;
let assetTotal = 0;
let characterEdit = null;
let references = new Set();
let referenceOffset = 0;
let assetEdit = null;
let cleanupModel = null;
let unmountModel = null;
let previewEpoch = 0;
let loadEpoch = 0;
let characterEpoch = 0;
const PAGE = 60;
const tags = (text) => text.split(',').map((value) => value.trim()).filter(Boolean);
function status(message) { $('#creative-status').textContent = message; }
async function request(path, method = 'GET', body) {
  const response = await api(prefix + path, { method, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  if (!response) throw new Error('The daemon is unavailable.');
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save this change. Check the fields.');
  return data;
}
function button(label, action, className = 'ghost small') {
  const node = el('button', className, label); node.type = 'button';
  node.onclick = async () => { node.disabled = true; try { await action(); } catch (error) { status(error.message); } finally { node.disabled = false; } };
  return node;
}
function selectedCharacter() { return characters.find((item) => item.id === selected); }
async function loadCharacters(more = false) {
  const epoch = ++characterEpoch;
  const data = await request(`/characters?limit=${PAGE}&offset=${more ? characters.length : 0}&q=${encodeURIComponent($('#character-search').value)}`);
  if (epoch !== characterEpoch) return;
  characters = more ? characters.concat(data.characters) : data.characters;
  characterTotal = data.total;
  paintCharacters();
}
function paintCharacters() {
  const list = $('#character-list'); list.replaceChildren();
  for (const character of characters) {
    const card = el('article', `character-card${selected === character.id ? ' on' : ''}`);
    if (character.reference_asset_ids.length) {
      const image = el('img', 'character-portrait'); image.src = `${prefix}/assets/${encodeURIComponent(character.reference_asset_ids[0])}/file`; image.alt = `${character.name} reference`; image.loading = 'lazy'; card.appendChild(image);
    } else card.appendChild(el('div', 'character-initial', [...character.name][0] || '◇'));
    card.append(el('h3', '', character.name), el('p', 'meta', character.project || 'Personal character'));
    if (character.description) card.appendChild(el('p', 'meta', character.description.slice(0, 150)));
    const actions = el('div', 'creative-actions');
    actions.append(button('Use', () => useCharacter(character)), button('Edit', () => editCharacter(character)), button('Assets', async () => {
      selected = selected === character.id ? '' : character.id; paintCharacters(); await loadAssets();
    }));
    card.appendChild(actions); list.appendChild(card);
  }
  if (!characters.length) list.appendChild(el('p', 'meta', $('#character-search').value.trim()
    ? 'No characters match this search.' : 'Create a character once. Keep their look, story notes, and reference images together for later scenes.'));
  $('#characters-more').hidden = characters.length >= characterTotal;
  $('#creative-selection').textContent = selectedCharacter()?.name || 'All assets';
}
async function loadAssets(more = false) {
  const revision = ++loadEpoch;
  const query = new URLSearchParams({ limit: PAGE, offset: more ? assets.length : 0,
    q: $('#asset-search').value, kind: $('#asset-kind').value, project: $('#asset-project-filter').value,
    character_id: selected });
  const data = await request(`/assets?${query}`);
  if (revision !== loadEpoch) return;
  assets = more ? assets.concat(data.assets) : data.assets; assetTotal = data.total;
  const grid = $('#creative-grid'); grid.replaceChildren();
  for (const asset of assets) {
    const card = button('', () => openAsset(asset), 'asset-card');
    if (asset.kind === 'image') { const image = el('img'); image.src = asset.url; image.alt = asset.name; image.loading = 'lazy'; card.appendChild(image); }
    else if (asset.kind === 'video') { const video = el('video'); video.src = asset.url; video.muted = true; video.preload = 'metadata'; card.appendChild(video); }
    else card.appendChild(el('div', 'asset-placeholder', '◇ 3D'));
    card.append(el('strong', '', asset.name), el('span', 'meta', `${asset.kind} · ${(asset.bytes / 1024 / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`));
    grid.appendChild(card);
  }
  if (!assets.length) {
    const filtered = selected || $('#asset-search').value.trim() || $('#asset-kind').value || $('#asset-project-filter').value.trim();
    const empty = el('div', 'creative-empty'); empty.append(el('div', 'creative-empty-icon', '◇'),
      el('h3', '', filtered ? 'No matching assets yet' : 'A home for the things you make'),
      el('p', '', filtered ? 'Try a different search or filter, or import a reference for this character.' : 'Bring in a reference image, video, or 3D model. Keep your favorite Studio results here to use again.'));
    if (filtered) empty.append(button('Clear filters', async () => {
      selected = ''; for (const id of ['#asset-search', '#asset-kind', '#asset-project-filter']) $(id).value = '';
      paintCharacters(); await loadAssets();
    }));
    else empty.append(button('Import your first asset', () => $('#asset-upload').click(), 'chip'));
    grid.appendChild(empty);
  }
  $('#assets-more').hidden = assets.length >= assetTotal;
  $('#asset-count').textContent = `${assetTotal.toLocaleString()} assets`;
}
async function upload(files, asReference = false) {
  for (const file of files) {
    if (file.size > 128 * 1024 * 1024) throw new Error(`${file.name} exceeds 128 MB.`);
    status(`Importing ${file.name}…`);
    const query = new URLSearchParams({ filename: file.name, project: asReference ? $('#character-form').elements.project.value : selectedCharacter()?.project || '' });
    if (selected && !asReference) query.set('character_id', selected);
    const response = await api(`${prefix}/assets/upload?${query}`, { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' } });
    if (!response) throw new Error('The daemon is unavailable.');
    const asset = await response.json();
    if (!response.ok) throw new Error(typeof asset.detail === 'string' ? asset.detail : 'This file could not be imported.');
    if (asReference) { if (asset.kind !== 'image') throw new Error('Character references must be images.'); references.add(asset.id); }
  }
  status('Assets imported locally.');
  if (asReference) await loadReferences();
  await Promise.all([loadAssets(), loadCharacters()]);
}
async function loadReferences(more = false) {
  if (!more) { referenceOffset = 0; $('#character-references').replaceChildren(); }
  const data = await request(`/assets?kind=image&limit=100&offset=${referenceOffset}`);
  referenceOffset += data.assets.length;
  for (const asset of data.assets) {
    const label = el('label', 'reference-choice');
    const input = el('input'); input.type = 'checkbox'; input.value = asset.id; input.checked = references.has(asset.id);
    input.onchange = () => { if (input.checked) references.add(asset.id); else references.delete(asset.id); };
    const image = el('img'); image.src = asset.url; image.alt = ''; image.loading = 'lazy';
    label.append(input, image, el('span', '', asset.name)); $('#character-references').appendChild(label);
  }
  $('#character-reference-more').hidden = referenceOffset >= data.total;
}
async function editCharacter(character = null) {
  // Asset imports, association edits and worker tools can all change references.
  // Load the current record so editing prose never removes a newly linked asset.
  if (character) character = await request(`/characters/${character.id}`);
  characterEdit = character?.id || null;
  references = new Set(character?.reference_asset_ids || []);
  $('#character-form').reset();
  for (const field of ['name', 'project', 'description', 'appearance_prompt', 'negative_prompt', 'consistency_notes']) {
    $('#character-form').elements[field].value = character?.[field] || '';
  }
  $('#character-form').elements.tags.value = (character?.tags || []).join(', ');
  $('#character-title').textContent = character ? `Edit ${character.name}` : 'Create a reusable character';
  $('#character-delete').hidden = !character;
  $('#character-error').textContent = '';
  $('#character-dialog').showModal();
  try { await loadReferences(); } catch (error) { $('#character-error').textContent = error.message; }
}
async function useCharacter(character, sheet = false) {
  const recipe = await request(`/characters/${character.id}/prepare`, 'POST');
  document.querySelector('[data-mode="studio"]').click(); $('#studio-simple').click();
  await applyCharacter(recipe, sheet ? 'Character reference sheet: full body, front, side, back, and a close-up portrait. Consistent outfit, proportions, colors, and lighting. Plain neutral background.' : '');
}
async function openAsset(asset) {
  const epoch = ++previewEpoch;
  unmountModel?.($('#asset-preview'));
  cleanupModel?.(); cleanupModel = null;
  const fresh = await request(`/assets/${asset.id}`);
  if (epoch !== previewEpoch) return;
  assetEdit = fresh;
  const stage = $('#asset-preview'); stage.replaceChildren(); $('#model-toolbar').replaceChildren();
  $('#asset-title').textContent = assetEdit.name;
  $('#asset-meta').textContent = JSON.stringify({ filename: assetEdit.filename, sha256: assetEdit.sha256, created: new Date(assetEdit.created_at * 1000).toLocaleString(), provenance: assetEdit.provenance }, null, 2);
  $('#asset-edit-form').reset();
  for (const field of ['name', 'description', 'project']) $('#asset-edit-form').elements[field].value = assetEdit[field];
  $('#asset-edit-form').elements.tags.value = assetEdit.tags.join(', ');
  const picker = $('#asset-characters'); picker.replaceChildren();
  // Associations outside the loaded character page are retained on save.
  for (const character of characters) {
    const label = el('label', 'reference-choice'); const input = el('input'); input.type = 'checkbox'; input.value = character.id; input.checked = assetEdit.character_ids.includes(character.id);
    label.append(input, el('span', '', character.name)); picker.appendChild(label);
  }
  $('#asset-download').href = assetEdit.download_url;
  $('#asset-error').textContent = '';
  $('#asset-dialog').showModal();
  if (assetEdit.kind === 'model') {
    stage.appendChild(el('p', 'meta', 'Loading 3D preview…'));
    try {
      const viewer = await import('./model-viewer.js');
      unmountModel = viewer.unmountModel;
      if (epoch !== previewEpoch) return;
      const cleanup = await viewer.mountModel(stage, $('#model-toolbar'), assetEdit);
      if (epoch !== previewEpoch) cleanup(); else cleanupModel = cleanup;
    } catch (error) { if (epoch === previewEpoch) stage.replaceChildren(el('p', 'meta', `Preview unavailable: ${error.message}. The original file can still be downloaded.`)); }
  } else {
    const media = el(assetEdit.kind === 'video' ? 'video' : 'img'); media.src = assetEdit.url;
    if (assetEdit.kind === 'video') media.controls = true; else media.alt = assetEdit.name;
    stage.appendChild(media);
  }
}
export async function keepMedia(item, character = null) {
  if (!item) return;
  await request('/assets/from-media', 'POST', { media_id: item.id, name: item.prompt?.slice(0, 200) || item.filename,
    project: character?.project || '', character_ids: character ? [character.id] : [] });
  status('Generated result saved to your creative library with its recipe.');
  document.dispatchEvent(new CustomEvent('omote:library-changed'));
}
export function wireCreative() {
  const pane = el('section'); pane.id = 'creative-workspace'; pane.hidden = true; pane.setAttribute('aria-label', 'Characters and creative assets');
  pane.innerHTML = `<header class="creative-head"><div><h2>Your story, in one place</h2><p class="meta">Keep characters consistent, reuse references, and explore the models you make.</p></div><div class="creative-actions"><button id="character-new" class="primary">New character</button><label class="chip">Import assets<input id="asset-upload" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.gif,.mp4,.webm,.glb,.gltf,.obj,.stl" hidden></label><a class="chip" href="/api/creative/export/archive" download>Export library</a></div></header>
    <p id="creative-status" class="meta" role="status"></p><div class="creative-layout"><aside><div class="creative-actions"><h3>Characters</h3><button id="creative-all" class="ghost small">All assets</button></div><input id="character-search" type="search" placeholder="Find a character…" aria-label="Find a character"><div id="character-list" class="creative-characters"></div><button id="characters-more" class="ghost small" hidden>More characters</button></aside>
    <section><div class="creative-actions"><h3 id="creative-selection">All assets</h3><span id="asset-count" class="meta"></span></div><div class="creative-filters"><input id="asset-search" type="search" placeholder="Search names and tags…" aria-label="Search assets"><input id="asset-project-filter" placeholder="Project filter" aria-label="Project filter"><select id="asset-kind" aria-label="Asset type"><option value="">All types</option><option value="image">Images</option><option value="video">Video</option><option value="model">3D models</option></select></div><div id="creative-grid" class="creative-grid"></div><button id="assets-more" class="ghost" hidden>Load more assets</button></section></div>`;
  $('#pane-studio').appendChild(pane);
  const characterDialog = el('dialog'); characterDialog.id = 'character-dialog';
  characterDialog.innerHTML = `<header><h2 id="character-title">Create a reusable character</h2><button id="character-close" class="ghost">Close</button></header><p class="meta">Describe the features that should stay the same. Add images for models that support references; consistency depends on the selected model.</p>
    <form id="character-form"><div class="creative-form-grid"><label>Name<input name="name" required maxlength="200" placeholder="Hana, the traveling botanist"></label><label>Project<input name="project" maxlength="500" placeholder="A story, campaign, or world"></label><label class="full">Story and personality<textarea name="description" maxlength="20000" placeholder="Who are they? What drives them?"></textarea></label><label class="full">Appearance to preserve<textarea name="appearance_prompt" maxlength="20000" placeholder="Face, hair, clothes, proportions, distinctive details…"></textarea></label><label>Continuity notes<textarea name="consistency_notes" maxlength="20000" placeholder="Keep the blue scarf and the silver leaf pin."></textarea></label><label>Avoid<textarea name="negative_prompt" maxlength="20000" placeholder="Unwanted changes or features"></textarea></label><label class="full">Tags, separated by commas<input name="tags" placeholder="hero, watercolor, chapter one"></label><div class="full"><div class="creative-actions"><strong>Reference images</strong><label class="chip">Add references<input id="character-upload" type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" hidden></label></div><p class="meta">Select the primary reference first. Originals stay in your library.</p><div id="character-references" class="reference-picker"></div><button id="character-reference-more" type="button" class="ghost small" hidden>More references</button></div></div>
    <p id="character-error" class="meta" role="status"></p><menu><button id="character-delete" type="button" class="ghost" hidden>Delete character</button><div class="spacer"></div><button type="submit" class="primary">Save character</button><button id="character-sheet" type="submit" class="ghost">Save &amp; make reference sheet</button></menu></form>`;
  const assetDialog = el('dialog'); assetDialog.id = 'asset-dialog';
  assetDialog.innerHTML = `<header><h2 id="asset-title">Asset</h2><button id="asset-close" class="ghost">Close</button></header><div id="asset-preview" class="asset-preview"></div><div id="model-toolbar" class="model-toolbar"></div><form id="asset-edit-form"><div class="creative-form-grid"><label>Name<input name="name" required maxlength="200"></label><label>Project<input name="project" maxlength="500"></label><label class="full">Description<textarea name="description" maxlength="20000"></textarea></label><label class="full">Tags<input name="tags"></label><div class="full"><strong>Characters in this asset</strong><div id="asset-characters" class="reference-picker"></div></div></div><p id="asset-error" class="meta" role="status"></p><menu><button id="asset-delete" type="button" class="ghost">Delete asset</button><a id="asset-download" class="chip" download>Download original</a><div class="spacer"></div><button type="submit" class="primary">Save details</button></menu></form><details><summary>Source &amp; generation recipe</summary><pre id="asset-meta"></pre></details>`;
  document.body.append(characterDialog, assetDialog);
  $('#studio-library').addEventListener('click', () => { Promise.all([loadCharacters(), loadAssets()]).catch((error) => status(error.message)); });
  $('#character-new').onclick = () => editCharacter().catch((error) => status(error.message));
  $('#character-close').onclick = () => characterDialog.close(); $('#asset-close').onclick = () => assetDialog.close();
  assetDialog.addEventListener('close', () => { ++previewEpoch; unmountModel?.($('#asset-preview')); cleanupModel?.(); cleanupModel = null; $('#asset-preview').replaceChildren(); });
  $('#creative-all').onclick = () => { selected = ''; paintCharacters(); loadAssets().catch((error) => status(error.message)); };
  const guard = (fn) => async (event) => { try { await fn(event); } catch (error) { status(error.message); } };
  $('#assets-more').onclick = guard(() => loadAssets(true)); $('#characters-more').onclick = guard(() => loadCharacters(true));
  $('#character-reference-more').onclick = guard(() => loadReferences(true));
  $('#asset-upload').onchange = guard(async (event) => { try { await upload(event.target.files); } finally { event.target.value = ''; } });
  $('#character-upload').onchange = async (event) => { try { await upload(event.target.files, true); } catch (error) { $('#character-error').textContent = error.message; } finally { event.target.value = ''; } };
  const grid = $('#creative-grid');
  grid.ondragover = (event) => { event.preventDefault(); grid.classList.add('dragging'); };
  grid.ondragleave = () => grid.classList.remove('dragging');
  grid.ondrop = guard(async (event) => { event.preventDefault(); grid.classList.remove('dragging'); await upload(event.dataTransfer.files); });
  let searchTimer; const search = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadAssets().catch((error) => status(error.message)), 200); };
  $('#asset-search').oninput = search; $('#asset-project-filter').oninput = search; $('#asset-kind').onchange = search;
  let characterTimer; $('#character-search').oninput = () => { clearTimeout(characterTimer); characterTimer = setTimeout(() => loadCharacters().catch((error) => status(error.message)), 200); };
  $('#character-form').onsubmit = async (event) => {
    event.preventDefault(); const button = event.submitter || event.currentTarget.querySelector('[type=submit]'); button.disabled = true;
    try {
      const body = Object.fromEntries(new FormData(event.currentTarget)); body.tags = tags(body.tags); body.reference_asset_ids = [...references];
      const character = await request(characterEdit ? `/characters/${characterEdit}` : '/characters', characterEdit ? 'PUT' : 'POST', body);
      characterDialog.close(); await loadCharacters(); status(`${character.name} saved. Use this character in any later scene.`);
      if (button.id === 'character-sheet') await useCharacter(character, true);
    } catch (error) { $('#character-error').textContent = error.message; } finally { button.disabled = false; }
  };
  $('#character-delete').onclick = async () => {
    if (!characterEdit || !confirm('Delete this character profile? Its original assets will remain in your library.')) return;
    try { await request(`/characters/${characterEdit}`, 'DELETE'); if (selected === characterEdit) selected = ''; characterDialog.close(); await loadCharacters(); await loadAssets(); }
    catch (error) { $('#character-error').textContent = error.message; }
  };
  $('#asset-edit-form').onsubmit = async (event) => {
    event.preventDefault(); const submit = event.submitter || event.currentTarget.querySelector('[type=submit]'); submit.disabled = true;
    try {
      const body = Object.fromEntries(new FormData(event.currentTarget)); body.tags = tags(body.tags);
      const visible = new Set([...$('#asset-characters').querySelectorAll('input')].map((input) => input.value));
      body.character_ids = assetEdit.character_ids.filter((id) => !visible.has(id));
      for (const input of $('#asset-characters').querySelectorAll('input:checked')) body.character_ids.push(input.value);
      assetEdit = await request(`/assets/${assetEdit.id}`, 'PATCH', body); $('#asset-title').textContent = assetEdit.name; $('#asset-error').textContent = 'Saved.'; await Promise.all([loadAssets(), loadCharacters()]);
    } catch (error) { $('#asset-error').textContent = error.message; } finally { submit.disabled = false; }
  };
  $('#asset-delete').onclick = async () => {
    if (!assetEdit || !confirm('Delete this original library file and remove it from character references? Export a copy first if needed.')) return;
    try { await request(`/assets/${assetEdit.id}`, 'DELETE'); assetDialog.close(); await Promise.all([loadAssets(), loadCharacters()]); }
    catch (error) { $('#asset-error').textContent = error.message; }
  };
  document.addEventListener('omote:library-changed', () => { if (!pane.hidden) Promise.all([loadAssets(), loadCharacters()]).catch((error) => status(error.message)); });
}
