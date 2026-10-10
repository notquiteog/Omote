/* The companion picker, and the one object the rest of the client talks to.
 *
 * Which creature is on is a preference, not a setting: it is kept in this
 * browser, never sent anywhere, and turning it off leaves nothing behind but
 * a faint mark to turn it back on with.
 *
 * One creature, many perches. The same animal is on the perch above the
 * composer, in the sidebar, next to the send button, and on an empty session —
 * all driven from one state, so it is never in two moods at once. Every perch is registered here; broadcasting to them is the whole of
 * the plumbing.
 */

import { Perch, portrait, stateForTool, setMotionPreference } from './engine.js';
import moth from './moth.js';
import gargoyle from './gargoyle.js';
import slime from './slime.js';
import mechbit from './mechbit.js';
import mandrake from './mandrake.js';

import fox from './fox.js';
import owl from './owl.js';
import octopus from './octopus.js';
import drone from './drone.js';

export const COMPANIONS = [moth, gargoyle, slime, mechbit, mandrake, fox, owl, octopus, drone];

const pickerStyle = document.createElement('link');
pickerStyle.rel = 'stylesheet';
pickerStyle.href = new URL('./picker.css', import.meta.url).href;
document.head.appendChild(pickerStyle);
const MOTION_STORE = 'omote.companion.motion';
const PERSONALITIES = {
  moth: 'Orbits its lamp while working, then bumps into the glass when something fails.',
  gargoyle: 'A stone guardian who wakes for work and holds still while waiting.',
  slime: 'Changes shape and expression to match the pace of your work.',
  mechbit: 'Shows scanlines, a drive light, and command progress on its little CRT.',
  mandrake: 'A shy plant who emerges from its pot as the work gets going.',
};

function motionSetting() {
  try { return localStorage.getItem(MOTION_STORE) || 'system'; } catch { return 'system'; }
}
setMotionPreference(motionSetting());

const STORE = 'omote.companion';
const DEFAULT = 'moth';
const NONE = 'none';

/* What the creature is doing, in words, for the places that have room for a
   line of text next to it. The companion is the fast glance; this is the same
   thing said plainly for anyone who would rather read it — and for a screen
   reader, which cannot see a moth at all. */
const CAPTIONS = {
  idle: 'ready',
  typing: 'listening',
  thinking: 'thinking',
  reading: 'reading',
  writing: 'writing files',
  running: 'running a command',
  browsing: 'browsing',
  desktop: 'using the desktop',
  grinding: 'still working',
  waiting: 'waiting for you',
  listening: 'listening',
  speaking: 'speaking',
  success: 'done',
  error: 'that went wrong',
};

export function caption(state) {
  return CAPTIONS[state] || CAPTIONS.idle;
}

function remembered() {
  // Wrapped for the same reason the session id is: a browser with site data
  // blocked throws on access rather than returning null.
  try {
    return localStorage.getItem(STORE);
  } catch {
    return null;
  }
}

function remember(id) {
  try {
    localStorage.setItem(STORE, id);
  } catch {
    /* private window */
  }
}

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

class Companion {
  constructor() {
    this.perches = [];
    this.pickers = [];
    this.watchers = [];
    this.def = null;
    this.id = null;
    this.state = 'idle';
    this.quietUntil = 0;
    this.flashTimer = null;
  }

  /* ------------------------------------------------------------- perches */

  /* A picker is a perch you can click: the creature, and a menu of the others
     behind it. There is more than one now — the sidebar has one too — and
     they all set the same preference. */
  mount(root, opts = {}) {
    if (!root) return null;
    const canvas = el('canvas');
    canvas.setAttribute('aria-hidden', 'true');

    const button = el('button', 'pet');
    button.type = 'button';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-expanded', 'false');
    button.appendChild(canvas);
    button.appendChild(el('span', 'empty', '◌'));

    const menu = el('div', `companion-menu ${opts.place || 'above'}`);
    menu.hidden = true;
    menu.id = `companion-picker-${this.pickers.length}`;
    button.setAttribute('aria-controls', menu.id);
    // A top-layer popover escapes the sidebar's backdrop/stacking context.
    // Keep it inside the perch so mobile navigation and focus still contain it.
    const topLayer = typeof menu.showPopover === 'function';
    if (topLayer) menu.setAttribute('popover', 'manual');
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-label', 'Choose a companion');

    root.appendChild(button);
    // Older desktop webviews need a body-level popup to escape that context.
    (topLayer ? root : document.body).appendChild(menu);

    const picker = { root, button, menu, topLayer, perch: new Perch(canvas, { scale: opts.scale }) };
    this.perches.push(picker.perch);
    this.pickers.push(picker);

    button.onclick = (e) => {
      e.stopPropagation();
      const open = menu.hidden;
      this.closeMenus();
      if (open) {
        menu.hidden = false;
        if (topLayer) menu.showPopover();
        button.setAttribute('aria-expanded', 'true');
        this.preview(picker, this.def || COMPANIONS[0]);
        this.positionMenu(picker);
        picker.search.focus();
      }
    };
    this.buildMenu(picker);

    // Anywhere else, and Escape, close it — the same two ways every menu in
    // every application closes.
    if (!this.closers) {
      this.closers = true;
      document.addEventListener('click', (event) => {
        if (!this.pickers.some((p) => p.root.contains(event.target) || p.menu.contains(event.target))) this.closeMenus();
      });
      const reposition = () => {
        for (const openPicker of this.pickers) if (!openPicker.menu.hidden) this.positionMenu(openPicker);
      };
      window.addEventListener('resize', reposition);
      window.visualViewport?.addEventListener('resize', reposition);
      window.visualViewport?.addEventListener('scroll', reposition);
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          const active = this.pickers.find((p) => !p.menu.hidden);
          if (active) {
            e.preventDefault();
            e.stopPropagation();
            this.closeMenus();
            active.button.focus();
          }
        }
      }, true);
      document.addEventListener('focusin', (e) => {
        if (!this.pickers.some((p) => p.root.contains(e.target) || p.menu.contains(e.target))) this.closeMenus();
      });
    }

    if (this.id === null) this.choose(remembered() || DEFAULT);
    else {
      picker.perch.use(this.def);
      picker.perch.set(this.state);
      this.dress(picker);
    }
    return picker;
  }

  /* A perch with no menu behind it: the ones that are purely a readout. They
     are made and unmade with whatever they sit on, so each hands back a way
     to remove itself. */
  attach(host, opts = {}) {
    if (!host) return { remove() {} };
    const canvas = el('canvas');
    canvas.setAttribute('aria-hidden', 'true');
    host.appendChild(canvas);

    const perch = new Perch(canvas, { scale: opts.scale });
    perch.use(this.def);
    this.perches.push(perch);
    // Whatever the agent is doing now, not `idle`: a perch made mid-turn
    // should not claim the turn is over.
    perch.set(this.state);

    return {
      canvas,
      remove: () => {
        perch.destroy();
        canvas.remove();
        const at = this.perches.indexOf(perch);
        if (at >= 0) this.perches.splice(at, 1);
      },
    };
  }

  /* Told whenever the state changes, for the captions that sit beside a
     creature and say the same thing in words. */
  watch(fn) {
    this.watchers.push(fn);
    fn(this.state, this.def);
  }

  announce() {
    for (const fn of this.watchers) fn(this.state, this.def);
  }

  /* --------------------------------------------------------------- menus */

  buildMenu(picker) {
    picker.menu.addEventListener('click', (event) => event.stopPropagation());
    const header = el('div', 'companion-header');
    const heading = el('div', 'companion-heading', 'Your companion');
    const close = el('button', 'companion-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close companion picker');
    close.onclick = () => { this.closeMenus(); picker.button.focus(); };
    header.append(heading, close);
    const note = el('p', 'companion-note', 'Reactions to your work. Your choice stays on this device.');
    picker.menu.append(header, note);

    const search = el('input', 'companion-search');
    search.type = 'search';
    search.placeholder = 'Find a companion';
    search.setAttribute('aria-label', 'Find a companion');
    picker.search = search;
    picker.menu.appendChild(search);

    const rows = el('div', 'companion-choices');
    picker.menu.appendChild(rows);
    picker.rows = rows;
    const choose = (id) => (e) => {
      e.stopPropagation();
      this.choose(id);
      this.closeMenus();
      picker.button.focus();
    };
    for (const def of COMPANIONS) {
      const row = el('button', 'row');
      row.type = 'button';
      const shot = portrait(def, 2);
      if (shot) { shot.setAttribute('aria-hidden', 'true'); row.appendChild(shot); }
      const text = el('div', 'text');
      text.appendChild(el('div', 'name', def.label));
      text.appendChild(el('div', 'blurb', def.blurb));
      row.appendChild(text);
      row.onclick = choose(def.id);
      row.onmouseenter = row.onfocus = () => this.preview(picker, def);
      row.dataset.id = def.id;
      rows.appendChild(row);
    }
    const off = el('button', 'row off');
    off.type = 'button';
    const text = el('div', 'text');
    text.appendChild(el('div', 'name', 'Nobody'));
    text.appendChild(el('div', 'blurb', 'an empty corner'));
    off.appendChild(text);
    off.onclick = choose(NONE);
    off.onmouseenter = off.onfocus = () => this.preview(picker, null);
    off.dataset.id = NONE;
    rows.appendChild(off);

    const noMatches = el('p', 'companion-note', 'No companions match this search.');
    noMatches.hidden = true;
    picker.menu.appendChild(noMatches);
    search.oninput = () => {
      const query = search.value.trim().toLocaleLowerCase();
      for (const row of rows.children) row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
      noMatches.hidden = [...rows.children].some((row) => !row.hidden);
    };
    picker.menu.addEventListener('keydown', (event) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      if (event.target !== search && !rows.contains(event.target)) return;
      if (event.target === search && ['Home', 'End'].includes(event.key)) return;
      const visible = [...rows.children].filter((row) => !row.hidden);
      if (!visible.length) return;
      event.preventDefault();
      const at = visible.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1
        : at < 0 ? (event.key === 'ArrowDown' ? 0 : visible.length - 1)
          : (at + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length;
      visible[next].focus();
    });

    const preview = el('div', 'companion-preview');
    const canvas = el('canvas');
    canvas.setAttribute('aria-hidden', 'true');
    picker.previewCanvas = canvas;
    picker.previewText = el('p', 'companion-note');
    preview.append(canvas, picker.previewText);
    picker.menu.appendChild(preview);
    const stateLabel = el('label', 'companion-control', 'Preview a reaction');
    const state = el('select');
    state.setAttribute('aria-label', 'Preview a reaction');
    for (const [id, label] of Object.entries(CAPTIONS)) {
      const option = el('option', '', label);
      option.value = id;
      state.appendChild(option);
    }
    picker.previewState = state;
    state.onchange = () => this.preview(picker, picker.previewDef);
    stateLabel.appendChild(state);
    picker.menu.appendChild(stateLabel);

    const motionLabel = el('label', 'companion-control', 'Motion');
    const motion = el('select');
    motion.setAttribute('aria-label', 'Companion motion');
    for (const [id, label] of [['system', 'Follow device setting'], ['still', 'Still images'], ['animated', 'Animate']]) {
      const option = el('option', '', label);
      option.value = id;
      motion.appendChild(option);
    }
    motion.value = ['still', 'animated'].includes(motionSetting()) ? motionSetting() : 'system';
    picker.motion = motion;
    motion.onchange = () => {
      try { localStorage.setItem(MOTION_STORE, motion.value); } catch { /* private window */ }
      setMotionPreference(motion.value);
      for (const other of this.pickers) other.motion.value = motion.value;
    };
    motionLabel.appendChild(motion);
    picker.menu.appendChild(motionLabel);
  }

  preview(picker, def) {
    picker.previewDef = def;
    if (!picker.previewPerch) picker.previewPerch = new Perch(picker.previewCanvas, { scale: 2 });
    picker.previewPerch.use(def);
    if (['success', 'error', 'typing'].includes(picker.previewState.value)) picker.previewPerch.flash(picker.previewState.value);
    else picker.previewPerch.set(picker.previewState.value);
    const reaction = def && def.behaviors && def.behaviors[picker.previewState.value];
    picker.previewText.textContent = def
      ? `${def.label}: ${reaction || def.personality || PERSONALITIES[def.id] || def.blurb}`
      : 'No companion. Agent status remains available in text.';
  }

  positionMenu(picker) {
    const rect = picker.button.getBoundingClientRect();
    const viewport = window.visualViewport;
    const left = viewport ? viewport.offsetLeft : 0;
    const top = viewport ? viewport.offsetTop : 0;
    const viewportWidth = viewport ? viewport.width : window.innerWidth;
    const viewportHeight = viewport ? viewport.height : window.innerHeight;
    picker.menu.style.maxWidth = `${Math.max(0, viewportWidth - 24)}px`;
    picker.menu.style.maxHeight = `${Math.max(0, viewportHeight - 24)}px`;
    const width = picker.menu.offsetWidth;
    const height = picker.menu.offsetHeight;
    const below = rect.bottom + 8;
    const wantedY = picker.menu.classList.contains('below') && below + height < top + viewportHeight - 12
      ? below : rect.top - height - 8;
    picker.menu.style.left = `${Math.max(left + 12, Math.min(rect.left, left + viewportWidth - width - 12))}px`;
    picker.menu.style.top = `${Math.max(top + 12, Math.min(wantedY, top + viewportHeight - height - 12))}px`;
  }

  openMenuWithin(root) {
    return this.pickers.find(p => !p.menu.hidden && root.contains(p.root))?.menu;
  }

  closeMenus() {
    for (const picker of this.pickers) {
      if (picker.menu.hidden) continue;
      if (picker.topLayer) picker.menu.hidePopover();
      picker.menu.hidden = true;
      picker.button.setAttribute('aria-expanded', 'false');
      if (picker.previewPerch) { picker.previewPerch.destroy(); picker.previewPerch = null; }
    }
  }

  /* ------------------------------------------------------------ choosing */

  choose(id) {
    const wanted = COMPANIONS.find((c) => c.id === id) || null;
    this.id = wanted ? wanted.id : NONE;
    remember(this.id);

    // A creature whose art does not check out is refused rather than drawn
    // wrong, so the perch has to end up looking empty even though something
    // was asked for — otherwise there is nothing left to click.
    let broken = [];
    for (const perch of this.perches) {
      broken = perch.use(wanted);
      perch.set(this.state);
    }
    for (const problem of broken) console.error(`companion: ${problem}`);
    this.def = broken.length ? null : wanted;

    // One class, so every spot that carries a creature can collapse together
    // when there is none. Turning the companion off should be the absence of
    // a thing, not a row of empty boxes.
    document.body.classList.toggle('companion-off', !this.def);
    for (const picker of this.pickers) this.dress(picker);
    this.announce();
  }

  dress(picker) {
    const def = this.def;
    picker.root.classList.toggle('off', !def);
    picker.button.title = def ? `${def.label} — click to change` : 'Companion';
    picker.button.setAttribute('aria-label', def ? `Companion: ${def.label}. Choose another.` : 'Choose a companion');
    for (const row of picker.menu.querySelectorAll('.row')) {
      row.setAttribute('aria-current', String(row.dataset.id === this.id));
      row.setAttribute('aria-pressed', String(row.dataset.id === this.id));
    }
  }

  /* Everything below is what the client actually calls. Each is a no-op when
     there is no companion, so nothing that reports state has to check first. */

  set(state) {
    if (state === this.state) return;
    this.state = state;
    clearTimeout(this.flashTimer);
    for (const perch of this.perches) perch.set(state);
    this.announce();
  }

  /* Reattaching to a session replays what you missed, and a moment that has
     already passed is not news: without this, opening the page would play out
     the last turn's success — or its failure — as though it had just
     happened. Held states still land, so the creature ends up in the state
     the session is actually in. */
  resumed() {
    this.quietUntil = performance.now() + 600;
  }

  flash(state) {
    if (performance.now() < this.quietUntil) return;
    for (const perch of this.perches) perch.flash(state);
    // A flash is a moment, not a condition, so the captions are told about it
    // and then told again when it lapses back to what is really going on.
    for (const fn of this.watchers) fn(state, this.def);
    clearTimeout(this.flashTimer);
    const reaction = this.def && this.def.states[state];
    this.flashTimer = setTimeout(() => this.announce(), (reaction && reaction.once) || 1200);
  }

  tool(name) {
    this.set(stateForTool(name));
  }

  typing() {
    for (const perch of this.perches) perch.typing();
  }
}

export const companion = new Companion();
