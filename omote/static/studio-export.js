/* Loaded only for the finishing screen. Every instance owns its project and polling. */
import { api, el } from './dom.js';

export function mountExport(mount, project, back) {
  let stopped = false, timer, setupTimer, pending = false, voiceRequest = 0;
  let takes = [], assets = [], exports = [], providers = [], voices = [];
  const selections = new Map();
  const base = `/api/creative/projects/${project.id}`;
  const status = el('p', 'meta'); status.setAttribute('role', 'status');
  const say = text => { if (!stopped) status.textContent = text; };
  async function request(path, method = 'GET', data) {
    const response = await api(path, { method, ...(data ? {
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    } : {}) });
    const result = await response?.json();
    if (!response?.ok) throw new Error(typeof result?.detail === 'string' ? result.detail : 'Could not complete that request. Try again.');
    return result;
  }
  function button(title, fn, cls = 'ghost') {
    const node = el('button', cls, title); node.type = 'button';
    node.onclick = async () => {
      node.disabled = true;
      try { await fn(); } catch (e) { say(e.message); } finally { node.disabled = false; }
    };
    return node;
  }
  function select(label, choices) {
    const wrap = el('label', 'project-field'), input = el('select');
    input.setAttribute('aria-label', label); wrap.append(el('span', '', label), input);
    for (const [id, text] of choices) { const option = el('option', '', text); option.value = id; input.append(option); }
    return [wrap, input];
  }
  function textField(label) {
    const wrap = el('label', 'project-field'), input = el('input');
    input.setAttribute('aria-label', label); wrap.append(el('span', '', label), input); return [wrap, input];
  }
  const toolbar = el('div', 'project-actions'); toolbar.append(button('Back to plan', back));
  const hero = el('div', 'export-intro');
  hero.append(el('span', 'export-eyebrow', 'THE FINAL CUT'), el('h3', '', 'Bring your story together'),
    el('p', 'meta', `${project.shots.length} ${project.shots.length === 1 ? 'shot' : 'shots'} · ${project.aspect_ratio} · Version ${project.revision}`));
  const layout = el('div', 'export-layout'), shots = el('div', 'export-shots'), settings = el('aside', 'export-settings');
  const [audioField, audio] = select('Sound', [['original', 'Keep clip sound'], ['silent', 'Silent'], ['narration', 'Generate voiceover']]);
  const captionLabel = el('label', 'export-checkbox'), captions = el('input'); captions.type = 'checkbox'; captions.checked = true;
  captionLabel.append(captions, document.createTextNode('Include on-screen captions'));
  const voicePanel = el('div', 'export-voice'); voicePanel.hidden = true;
  const [providerField, provider] = select('Voice provider', [['', 'Choose a provider']]);
  const [modelField, model] = textField('Voice model');
  const modelList = el('datalist'); modelList.id = 'export-models'; model.setAttribute('list', modelList.id);
  const [voiceField, voice] = select('Voice', [['', 'Provider default']]);
  const voiceNote = el('p', 'meta');
  voicePanel.append(providerField, modelField, modelList, voiceField, voiceNote);
  const explanation = el('p', 'meta', 'Fits your images and videos without cropping. Short clips hold their final frame.');
  const generate = button('Export video', async () => {
    if (pending) return;
    if (selections.size !== project.shots.length) throw new Error('Choose an image or video for every shot.');
    if (audio.value === 'narration' && (!provider.value || !model.value.trim())) throw new Error('Choose a voice provider and model.');
    pending = true; choicesBox.disabled = true; say('Starting export…');
    try {
      await request(base + '/exports', 'POST', { base_revision: project.revision,
        selections: project.shots.map(s => ({ shot_id: s.id, ...selections.get(s.id) })),
        captions: captions.checked, audio: audio.value, provider: provider.value, model: model.value.trim(), voice: voice.value });
      say('Export started. You can leave this screen while it finishes.');
      await refresh();
    } finally { pending = false; choicesBox.disabled = false; }
  }, 'primary');
  const choicesBox = el('fieldset', 'export-controls');
  choicesBox.append(audioField, captionLabel, voicePanel, explanation, generate);
  let setupJob;
  const cancelSetup = button('Cancel setup', async () => {
    if (setupJob) { await request('/api/media/jobs/' + setupJob, 'DELETE'); await checkSetup(); }
  }); cancelSetup.hidden = true;
  const setup = button('Set up video export', async () => {
    setupJob = (await request('/api/media/setup/video', 'POST')).id;
    setup.hidden = true; cancelSetup.hidden = false; await checkSetup();
  }, 'primary'); setup.hidden = true;
  choicesBox.append(setup, cancelSetup);
  async function checkSetup() {
    clearTimeout(setupTimer);
    try {
      const job = await request('/api/media/jobs/' + setupJob);
      if (stopped) return;
      say(job.error || job.note || 'Setting up video export…');
      if (job.state === 'done') { generate.disabled = false; cancelSetup.hidden = true; }
      else if (['failed', 'cancelled'].includes(job.state)) { setup.hidden = false; cancelSetup.hidden = true; }
      else setupTimer = setTimeout(checkSetup, 1000);
    } catch (e) { say(e.message); setup.hidden = false; cancelSetup.hidden = true; }
  }
  const viewFinished = button('View finished video', () => {
    history.querySelector('video')?.scrollIntoView({ block: 'center' });
  }); viewFinished.hidden = true;
  settings.append(el('h3', '', 'Make it yours'), choicesBox, status, viewFinished);
  layout.append(shots, settings);
  const history = el('section', 'export-history'); history.setAttribute('aria-label', 'Finished videos');
  mount.append(toolbar, hero, layout, history);

  function updateVoices() {
    const current = voice.value; voice.replaceChildren();
    for (const item of [{ id: '', name: 'Provider default' }, ...voices.filter(v => !v.model || v.model === model.value)]) {
      const option = el('option', '', item.name || item.label || item.id); option.value = item.id; voice.append(option);
    }
    if ([...voice.options].some(o => o.value === current)) voice.value = current;
  }
  model.oninput = updateVoices;
  audio.onchange = () => {
    voicePanel.hidden = audio.value !== 'narration';
    explanation.textContent = audio.value === 'narration'
      ? 'Voiceover replaces clip sound. Shots extend to fit the full narration. Only the script is sent to your chosen voice provider.'
      : 'Fits your images and videos without cropping. Short clips hold their final frame.';
    if (audio.value === 'narration' && !providers.length) say('Connect a voice provider in Settings, then reopen this screen.');
    else say('');
  };
  provider.onchange = async () => {
    const serial = ++voiceRequest;
    model.value = ''; voices = []; updateVoices(); modelList.replaceChildren();
    const chosen = providers.find(p => p.id === provider.value);
    voiceNote.textContent = chosen ? chosen.local ? 'Uses your local voice provider.' : 'Generating voiceover may incur charges from this provider.' : '';
    if (!chosen) return;
    say('Loading voice choices…');
    try {
      const result = await request(base + '/export-voices?provider=' + encodeURIComponent(chosen.id));
      if (stopped || serial !== voiceRequest) return;
      voices = result.voices || [];
      for (const item of result.models || []) {
        const option = el('option', '', item.name || item.id); option.value = item.id; modelList.append(option);
      }
      model.value = result.model || ''; updateVoices();
      if ([...voice.options].some(o => o.value === result.voice)) voice.value = result.voice;
      say('Choose a voice model, then export when ready.');
    } catch (e) { if (serial === voiceRequest) say(e.message); }
  };

  function drawShots() {
    shots.replaceChildren();
    project.shots.forEach((shot, index) => {
      const choices = [];
      for (const take of takes.filter(t => t.shot_id === shot.id && t.result.state === 'done')) {
        for (const media of take.result.media || []) if (/^(image|video)\//.test(media.media_type)) choices.push({
          value: { media_id: media.id }, key: 'media:' + media.id, url: media.url, mime: media.media_type,
          title: `Take · v${take.revision} · ${take.result.provider} · ${media.id.slice(-6)}`,
        });
      }
      for (const asset of assets.filter(a => ['image', 'video'].includes(a.kind))) choices.push({
        value: { asset_id: asset.id }, key: 'asset:' + asset.id, url: `/api/creative/assets/${asset.id}/file`,
        mime: asset.media_type, title: 'Library · ' + asset.name,
      });
      const card = el('article', 'export-shot'); card.dataset.shot = shot.id;
      const preview = el('div', 'export-preview'), content = el('div', 'export-shot-content');
      content.append(el('span', 'export-eyebrow', `SHOT ${String(index + 1).padStart(2, '0')} · ${shot.seconds}s`),
        el('strong', '', shot.title));
      const [pickerField, picker] = select(`Visual for ${shot.title}`, [['', 'Choose an image or video'], ...choices.map(c => [c.key, c.title])]);
      const current = selections.get(shot.id);
      const selected = current ? choices.find(c => c.key === (current.media_id ? 'media:' + current.media_id : 'asset:' + current.asset_id))
        : choices.find(c => c.value.media_id);
      if (selected) picker.value = selected.key;
      function choose() {
        preview.replaceChildren();
        const item = choices.find(c => c.key === picker.value);
        if (item) {
          selections.set(shot.id, item.value);
          const node = el(item.mime.startsWith('video/') ? 'video' : 'img'); node.src = item.url;
          if (node.tagName === 'VIDEO') { node.controls = true; node.preload = 'metadata'; } else node.alt = shot.title;
          preview.append(node);
        } else { selections.delete(shot.id); preview.append(el('span', 'meta', 'Add a visual')); }
      }
      picker.onchange = choose; choose(); content.append(pickerField);
      const upload = el('input'); upload.type = 'file'; upload.hidden = true; upload.accept = 'image/png,image/jpeg,image/webp,video/mp4,video/webm';
      upload.onchange = async () => {
        if (!upload.files[0] || pending) return;
        pending = true; choicesBox.disabled = true;
        try {
          say('Adding visual…');
          const file = upload.files[0];
          const response = await api('/api/creative/assets/upload?filename=' + encodeURIComponent(file.name), { method: 'POST', body: file });
          const result = await response?.json();
          if (!response?.ok) throw new Error(result?.detail || 'Could not upload that file.');
          if (stopped) return;
          assets.unshift(result); selections.set(shot.id, { asset_id: result.id }); drawShots(); say('Visual added to your library.');
        } catch (e) { say(e.message); } finally { pending = false; choicesBox.disabled = false; }
      };
      content.append(button('Upload visual', () => { if (!pending) upload.click(); }), upload);
      if (shot.caption) content.append(el('p', 'export-caption', shot.caption));
      if (shot.narration) {
        const script = el('details', 'export-script'); script.append(el('summary', '', 'Voiceover script'), el('p', 'meta', shot.narration));
        content.append(script);
      }
      card.append(preview, content); shots.append(card);
    });
  }
  function drawExports() {
    history.replaceChildren();
    viewFinished.hidden = !exports.some(item => item.result.state === 'done' && item.result.media?.length);
    if (!exports.length) return;
    history.append(el('h3', '', 'Your exports'));
    for (const item of exports) {
      const job = item.result, card = el('article', 'export-result');
      card.append(el('strong', '', `Version ${item.revision} · ${job.state === 'done' ? 'Ready' : job.state}`));
      const note = el('p', 'meta', job.error || job.note); note.setAttribute('role', 'status'); card.append(note);
      if (['queued', 'running'].includes(job.state)) {
        const progress = el('progress'); progress.max = 1; progress.setAttribute('aria-label', 'Export progress');
        if (job.progress !== null) progress.value = job.progress;
        card.append(progress, button('Cancel export', async () => { await request(base + '/exports/' + job.id, 'DELETE'); await refresh(); }));
      }
      for (const media of job.media || []) {
        const video = el('video'); video.src = media.url; video.controls = true; video.preload = 'metadata';
        const link = el('a', 'ghost', 'Download MP4'); link.href = base + '/exports/' + job.id + '/file'; link.download = project.name + '.mp4';
        card.append(video, link);
      }
      history.append(card);
    }
  }
  async function refresh() {
    clearTimeout(timer);
    try {
      const result = await request(base + '/exports');
      if (stopped) return;
      if (result.exports.some(item => item.result.state === 'done' && exports.some(old =>
        old.job_id === item.job_id && ['queued', 'running'].includes(old.result.state)))) {
        say('Your video is ready. Play or download it below.');
      }
      // Leave video elements intact while playing completed exports.
      if (JSON.stringify(exports) !== JSON.stringify(result.exports)) { exports = result.exports; drawExports(); }
    } catch (e) { say(e.message); }
    if (!stopped && exports.some(e => ['queued', 'running'].includes(e.result.state))) timer = setTimeout(refresh, 1500);
  }
  async function initialize() {
    generate.disabled = true; say('Loading your shots…');
    try {
      const [options, takeList, assetList, exportList] = await Promise.all([
        request(base + '/export-options'), request(base + '/takes'), request('/api/creative/assets?limit=200'), request(base + '/exports'),
      ]);
      if (stopped) return;
      providers = options.providers; takes = takeList.takes; assets = assetList.assets; exports = exportList.exports;
      const last = exports[0]?.snapshot.settings;
      for (const selection of last?.selections || []) {
        if (!project.shots.some(s => s.id === selection.shot_id)) continue;
        const { shot_id, media_id, asset_id } = selection; selections.set(shot_id, media_id ? { media_id } : { asset_id });
      }
      for (const item of providers) {
        const option = el('option', '', item.label + (item.local ? ' · Local' : '')); option.value = item.id; provider.append(option);
      }
      drawShots(); drawExports();
      generate.disabled = !options.available;
      setup.hidden = options.available || !options.can_install;
      say(options.available ? 'Choose your visuals, then export a finished MP4 on this device.'
        : options.can_install ? 'Set up video export once. Omote downloads and checks the tools for your computer.'
        : 'Ask the agent to set up video export for this computer.');
      if (exports.some(e => ['queued', 'running'].includes(e.result.state))) timer = setTimeout(refresh, 1500);
    } catch (e) { say(e.message); }
  }
  initialize();
  return () => { stopped = true; clearTimeout(timer); clearTimeout(setupTimer); voiceRequest++; };
}
