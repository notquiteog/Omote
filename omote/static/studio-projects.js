/* Guided production plans; loaded only when Projects is opened. */
import { $, api, el } from './dom.js';

let dialog, body, message, heading, catalog, project, dirty = false, stageCleanup;
let assets = [], characters = [], onPrepare, busy = false;
let settingsExpanded = false, directionExpanded = false;
const scriptExpanded = new Set();
const fields = ['name', 'template', 'brief', 'aspect_ratio', 'character_id', 'reference_asset_ids',
  'source_asset_id', 'remix', 'effect', 'direction', 'shots', 'stage'];
const documentOf = () => Object.fromEntries(fields.map(key => [key, project[key]]));
const note = text => { message.textContent = text; };
const change = () => { dirty = true; note('Unsaved changes'); };
async function request(path, method = 'GET', data) {
  const response = await api('/api/creative' + path, { method,
    ...(data ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}) });
  if (!response) throw new Error('Omote is not reachable. Your draft is still here.');
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check the project fields and try again.');
  return result;
}
function action(label, fn, cls = 'ghost') {
  const button = el('button', cls, label); button.type = 'button';
  button.onclick = async () => {
    if (busy) return;
    busy = true; button.disabled = true;
    try { await fn(); } catch (error) { note(error.message); }
    finally { busy = false; button.disabled = false; }
  };
  return button;
}
function field(label, value, update, { options, multiline, type, max, min } = {}) {
  const wrap = el('label', 'project-field'); wrap.append(el('span', '', label));
  let input;
  if (options) {
    input = el('select');
    for (const option of options) {
      const [id, title] = Array.isArray(option) ? option : [option, option];
      const node = el('option', '', title); node.value = id; input.append(node);
    }
  } else { input = el(multiline ? 'textarea' : 'input'); if (multiline) input.rows = 3; else input.type = type || 'text'; }
  if (max) { input.max = max; input.maxLength = max; }
  if (min !== undefined) input.min = min;
  input.setAttribute('aria-label', label);
  input.value = value ?? ''; input.oninput = () => { update(type === 'number' ? Number(input.value) : input.value); change(); };
  wrap.append(input); return wrap;
}
async function inventory() {
  const [a, c] = await Promise.all([request('/assets?limit=200'), request('/characters?limit=200')]);
  assets = a.assets; characters = c.characters;
  if (project?.character_id && !characters.some(c => c.id === project.character_id)) {
    try { characters.push(await request('/characters/' + encodeURIComponent(project.character_id))); } catch { /* The saved plan keeps the missing ID for an explicit correction. */ }
  }
}
async function save() {
  if (!project) return;
  if (!dirty) return;
  // Freeze the request; edits made during the request stay in the local draft.
  const snapshot = JSON.stringify(documentOf());
  const saved = await request(`/projects/${project.id}`, 'PUT', { base_revision: project.revision, document: JSON.parse(snapshot) });
  project.revision = saved.revision;
  dirty = snapshot !== JSON.stringify(documentOf());
  note(dirty ? 'Saved; newer changes are still in this draft.' : 'Saved locally · Version ' + saved.revision);
}
function clearBody() { stageCleanup?.(); stageCleanup = null; body.replaceChildren(); }
async function home() {
  if (dirty) await save();
  const list = await request('/projects');
  project = null; dirty = false; clearBody(); heading.textContent = 'What would you like to create?';
  note('Plans and 3D previews stay on this device. Generation uses the provider you choose.');
  const grid = el('div', 'project-templates');
  for (const template of catalog.templates) {
    const card = action('', () => start(template), 'project-template');
    card.append(el('span', 'project-template-icon', template.icon), el('strong', '', template.name), el('span', 'meta', template.description));
    grid.append(card);
  }
  body.append(grid);
  const picker = el('input'); picker.type = 'file'; picker.accept = '.json,application/json'; picker.hidden = true;
  picker.onchange = async () => {
    const file = picker.files[0]; if (!file || busy) return;
    busy = true;
    try {
      if (file.size > 1024 * 1024) throw new Error('Keep a project below 1 MB.');
      const imported = JSON.parse(await file.text());
      const document = Object.fromEntries(fields.filter(key => key in imported).map(key => [key, imported[key]]));
      project = await request('/projects/import', 'POST', { document });
      dirty = false; await inventory(); editor();
    } catch (error) { note(error.message); } finally { busy = false; picker.value = ''; }
  };
  const imports = el('div', 'project-actions project-import'); imports.append(action('Import plan', () => picker.click()), picker); body.append(imports);
  if (list.projects.length) {
    body.append(el('h3', '', 'Your projects'));
    const recent = el('div', 'project-recents');
    for (const row of list.projects) {
      const button = action('', async () => { project = await request('/projects/' + row.id); await inventory(); editor(); }, 'project-recent');
      button.append(el('strong', '', row.name), el('span', 'meta', `${catalog.templates.find(t => t.id === row.template)?.name || row.template} · v${row.revision}`));
      recent.append(button);
    }
    body.append(recent);
  }
}
function start(template) {
  clearBody(); heading.textContent = template.name; note(template.hint);
  let name = '', brief = '';
  const form = el('form', 'project-start');
  form.append(field('Project name', '', value => { name = value; }, { max: 200 }),
    field('What are you making?', '', value => { brief = value; }, { multiline: true, max: 20000 }));
  const actions = el('div', 'project-actions');
  actions.append(action('Back', home), action('Create plan', async () => {
    if (!name.trim()) throw new Error('Give your project a name.');
    project = await request('/projects', 'POST', { template: template.id, name, brief });
    dirty = false; await inventory(); editor();
  }, 'primary'));
  form.onsubmit = e => e.preventDefault(); form.append(actions); body.append(form); $('input', form).focus();
}
function references(box, owner, key = 'reference_asset_ids', video = false) {
  const section = el('div', 'project-references');
  const title = el('span', 'meta', video ? 'Source video' : 'Reference images'); section.append(title);
  const chips = el('div', 'project-reference-chips');
  const ids = video ? (owner[key] ? [owner[key]] : []) : owner[key];
  for (const id of ids) {
    const asset = assets.find(a => a.id === id);
    const chip = action(`${asset?.name || 'Missing reference'} ×`, () => {
      if (video) owner[key] = ''; else owner[key] = owner[key].filter(value => value !== id);
      change(); editor();
    }, 'chip');
    chip.title = 'Remove reference'; chips.append(chip);
  }
  section.append(chips);
  const available = assets.filter(a => a.kind === (video ? 'video' : 'image') && !ids.includes(a.id));
  const select = el('select'); select.setAttribute('aria-label', video ? 'Choose source video' : 'Choose reference image');
  const placeholder = el('option', '', 'Choose from library'); placeholder.value = ''; select.append(placeholder);
  for (const asset of available) { const option = el('option', '', asset.name); option.value = asset.id; select.append(option); }
  select.onchange = () => { if (!select.value) return; if (video) owner[key] = select.value; else owner[key].push(select.value); change(); editor(); };
  const picker = el('input'); picker.type = 'file'; picker.hidden = true;
  picker.accept = video ? 'video/mp4,video/webm' : 'image/png,image/jpeg,image/webp';
  picker.onchange = async () => {
    const file = picker.files[0]; if (!file || busy) return;
    busy = true;
    try {
      note('Adding reference…');
      const response = await api('/api/creative/assets/upload?filename=' + encodeURIComponent(file.name), { method: 'POST', body: file });
      const result = await response?.json();
      if (!response?.ok) throw new Error(result?.detail || 'Could not add that file.');
      assets.unshift(result); if (video) owner[key] = result.id; else owner[key].push(result.id);
      change(); editor();
    } catch (error) { note(error.message); } finally { busy = false; }
  };
  const row = el('div', 'project-actions'); row.append(select, action('Upload', () => picker.click()), picker);
  if (!video && ids.length >= 12) { select.disabled = true; row.querySelector('button').disabled = true; }
  section.append(row); box.append(section);
}
async function prepare(shot, kind) {
  await save();
  if (dirty) throw new Error('Save your latest changes before preparing this shot.');
  const result = await request(`/projects/${project.id}/shots/${shot.id}/prepare`, 'POST');
  await onPrepare({ ...result, kind }); dialog.close();
}
function editor() {
  clearBody(); heading.textContent = project.name;
  const template = catalog.templates.find(t => t.id === project.template);
  note(dirty ? 'Unsaved changes' : template.hint);
  const top = el('div', 'project-actions project-toolbar');
  top.append(action('All projects', home), action('Save plan', save, 'primary'), action('Ask AI', async () => {
    await save(); if (dirty) throw new Error('Save your latest changes first.');
    dialog.close(); document.dispatchEvent(new CustomEvent('omote:studio-assistant', { detail: {
      id: project.id, revision: project.revision, name: project.name, template: project.template,
    } }));
  }), action('Reload plan', async () => {
    if (dirty && !confirm('Replace your unsaved draft with the latest saved plan? Export your draft first to keep a copy.')) return;
    project = await request('/projects/' + project.id); dirty = false; await inventory(); editor();
  }), action('Export plan', () => download({ format: 'omote.studio.v1', ...documentOf() }, project.name + '.json')));
  body.append(top);
  const brief = el('div', 'project-brief');
  brief.append(field('Creative brief', project.brief, value => { project.brief = value; }, { multiline: true, max: 20000 }));
  const settings = el('details', 'project-settings'); settings.open = settingsExpanded;
  settings.ontoggle = () => { settingsExpanded = settings.open; };
  settings.append(el('summary', '', `Project settings · ${project.aspect_ratio}`));
  const settingsGrid = el('div', 'project-direction-grid');
  settingsGrid.append(field('Project name', project.name, value => { project.name = value; }, { max: 200 }),
    field('Format', project.aspect_ratio, value => { project.aspect_ratio = value; }, { options: [['16:9', 'Landscape · 16:9'], ['9:16', 'Portrait · 9:16'], ['1:1', 'Square · 1:1'], ['4:5', 'Feed · 4:5']] }),
    field('Character', project.character_id, value => { project.character_id = value; }, {
      options: [['', 'No character'], ...characters.map(c => [c.id, c.name])],
    }));
  settings.append(settingsGrid); body.append(settings);
  references(brief, project);
  if (project.template === 'remix') {
    references(brief, project, 'source_asset_id', true);
    brief.append(field('Transformation', project.remix, v => { project.remix = v; }, {
      options: [['restyle', 'Restyle the scene'], ['motion', 'Transfer motion'], ['replace', 'Replace the subject']],
    }));
  }
  if (project.template === 'effects') brief.append(field('Effect', project.effect, v => { project.effect = v; }, {
    options: [['none', 'Describe your own'], ['orbit', 'Orbit reveal'], ['miniature', 'Miniature world'], ['particles', 'Particle dissolve'], ['zoom', 'Aerial pullback'], ['freeze', 'Frozen moment']],
  }));
  body.append(brief);
  const direction = el('details', 'project-direction'); direction.open = directionExpanded;
  direction.ontoggle = () => { directionExpanded = direction.open; }; direction.append(el('summary', '', 'Look & camera'));
  direction.append(el('p', 'meta', 'These choices guide the prompt. The selected model determines how closely it follows them.'));
  const controls = el('div', 'project-direction-grid');
  for (const [key, choices] of Object.entries(catalog.direction)) controls.append(field(key[0].toUpperCase() + key.slice(1),
    project.direction[key] || 'Auto', value => { project.direction[key] = value; }, { options: choices }));
  direction.append(controls); body.append(direction);
  if (project.template === 'scene') body.append(action('Open 3D stage', openStage, 'primary'));
  const storyboard = el('div', 'project-storyboard');
  project.shots.forEach((shot, index) => {
    const card = el('article', 'project-shot'); card.dataset.shot = shot.id;
    const header = el('div', 'project-shot-header'); header.append(el('span', 'project-shot-number', String(index + 1).padStart(2, '0')),
      field('Shot title', shot.title, value => { shot.title = value; }, { max: 200 }),
      field('Seconds', shot.seconds, value => { shot.seconds = value; }, { type: 'number', min: 1, max: 30 }));
    card.append(header, field('What happens in this shot?', shot.prompt, value => { shot.prompt = value; }, { multiline: true, max: 20000 }));
    const showScript = ['faceless', 'marketing'].includes(project.template) || shot.narration || shot.caption || scriptExpanded.has(shot.id);
    if (showScript) {
      const script = el('div', 'project-script');
      script.append(field('Voiceover script', shot.narration, value => { shot.narration = value; }, { multiline: true, max: 20000 }),
        field('On-screen caption', shot.caption, value => { shot.caption = value; }, { max: 2000 })); card.append(script);
    }
    if (shot.reference_asset_ids.length) references(card, shot);
    const actions = el('div', 'project-actions');
    if (project.template !== 'remix') actions.append(action('Prepare image', () => prepare(shot, 'image'), 'primary'));
    actions.append(action('Prepare video', () => prepare(shot, 'video'), project.template === 'remix' ? 'primary' : 'ghost'));
    const more = el('details', 'project-shot-more'); more.append(el('summary', '', 'Edit shot'));
    const edit = el('div', 'project-actions');
    if (!showScript) edit.append(action('Add voiceover & caption', () => { scriptExpanded.add(shot.id); editor(); }));
    if (index) edit.append(action('Move up', () => {
      [project.shots[index - 1], project.shots[index]] = [shot, project.shots[index - 1]]; change(); editor();
    }));
    if (index < project.shots.length - 1) edit.append(action('Move down', () => {
      [project.shots[index], project.shots[index + 1]] = [project.shots[index + 1], shot]; change(); editor();
    }));
    if (project.shots.length < 32) edit.append(action('Duplicate', () => {
      project.shots.splice(index + 1, 0, { ...structuredClone(shot), id: crypto.randomUUID(), title: shot.title + ' copy' }); change(); editor();
    }));
    if (project.shots.length > 1) edit.append(action('Remove shot', () => { project.shots.splice(index, 1); change(); editor(); }));
    more.append(edit); actions.append(more); card.append(actions); storyboard.append(card);
  });
  body.append(el('h3', '', `Your shots · ${project.shots.reduce((n, s) => n + s.seconds, 0)} seconds planned`), storyboard);
  if (['faceless', 'marketing'].includes(project.template)) body.append(el('p', 'meta', 'Generate your visuals, then choose Make final video to add captions and voiceover.'));
  const footer = el('div', 'project-actions');
  if (project.shots.length < 32) footer.append(action('Add shot', () => {
    project.shots.push({ id: crypto.randomUUID(), title: 'New shot', prompt: '', narration: '', caption: '', seconds: 5, reference_asset_ids: [] });
    change(); editor(); body.lastElementChild.scrollIntoView({ block: 'nearest' });
  }));
  footer.append(action('Make final video', openExport, 'primary'), action('Show takes', showTakes), action('Delete project', async () => {
    if (!confirm('Delete this project and its plan? Generated files and library assets will stay.')) return;
    await request('/projects/' + project.id, 'DELETE'); dirty = false; project = null; await home();
  })); body.append(footer);
}
async function openExport() {
  await save();
  if (dirty) throw new Error('Save your latest changes before exporting.');
  const module = await import('./studio-export.js');
  if (!dialog.open) return;
  clearBody(); heading.textContent = project.name; note('');
  stageCleanup = module.mountExport(body, structuredClone(project), editor);
}
async function showTakes() {
  const ident = project.id;
  const result = await request(`/projects/${ident}/takes`);
  $('#project-takes')?.remove();
  const section = el('section', 'project-takes'); section.id = 'project-takes'; section.append(el('h3', '', 'Generated takes'));
  if (!result.takes.length) section.append(el('p', 'meta', 'Prepare a shot, choose a model and generate it. Its results will appear here.'));
  for (const take of result.takes) {
    const row = el('article', 'project-take');
    row.append(el('strong', '', take.snapshot.title), el('span', 'meta', `v${take.revision} · ${take.result.provider} · ${take.result.state}`));
    if (take.result.error || take.result.note) row.append(el('p', 'meta', take.result.error || take.result.note));
    for (const media of take.result.media || []) {
      const node = el(media.kind === 'video' ? 'video' : 'img'); node.src = `/api/media/${media.id}/file`;
      if (media.kind === 'video') node.controls = true; else node.alt = take.snapshot.title;
      row.append(node);
    }
    section.append(row);
  }
  body.append(section); section.scrollIntoView({ block: 'nearest' });
}
async function openStage() {
  const stageProject = project;
  const module = await import('./studio-stage.js');
  clearBody(); heading.textContent = 'Stage your scene'; note('Drag objects to place them. Drag empty space to orbit; scroll to zoom.');
  const toolbar = el('div', 'project-actions'); toolbar.append(action('Back to plan', () => editor()), action('Save scene', save, 'primary'));
  const mount = el('div', 'project-stage');
  body.append(toolbar, mount);
  stageCleanup = module.mountStage(mount, project.stage, project.aspect_ratio, change, async blob => {
    const response = await api('/api/creative/assets/upload?filename=stage-frame.png&name=Stage%20reference', { method: 'POST', body: blob });
    const asset = await response?.json();
    if (!response?.ok) throw new Error(asset?.detail || 'Could not save the frame.');
    assets.unshift(asset);
    if (project !== stageProject) throw new Error('Frame saved to the library. Reopen the original project to attach it.');
    const shot = project.shots[0];
    // Capture is an explicit replacement of this shot's blocking reference.
    shot.reference_asset_ids = [asset.id]; change(); await save(); note('Frame saved as the first shot’s reference. Return to the plan to generate.');
  });
}
function download(data, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = el('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function openProjects(prepareCallback) {
  onPrepare = prepareCallback;
  if (!dialog) {
    dialog = el('dialog', 'studio-project-dialog'); dialog.id = 'studio-projects'; dialog.setAttribute('aria-label', 'Studio projects');
    const header = el('div', 'project-dialog-header'); heading = el('h2', '', 'Studio projects');
    header.append(heading, action('Close', () => dialog.close()));
    message = el('p', 'meta project-status'); message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
    body = el('div', 'project-body'); dialog.append(header, message, body); document.body.append(dialog);
    dialog.addEventListener('close', () => { stageCleanup?.(); stageCleanup = null; });
  }
  dialog.showModal();
  try {
    catalog ||= await request('/projects/catalog');
    if (project) {
      if (!dirty) { project = await request('/projects/' + project.id); await inventory(); }
      editor();
    } else await home();
  } catch (error) { note(error.message); }
}
