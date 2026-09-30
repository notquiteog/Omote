/* The two things that are not a message: looking something up, and mail.
 *
 * Both were modes. Both are also tools the agent has always had — `web_search`
 * and `research` on one side, `mail` on the other — and the tools are the
 * better half of each, because they are the half that composes: "what came in
 * today, and what did the Acme thread say about the invoice" is one turn
 * answered by both, and it could not be if reading mail were a place you had to
 * go to first.
 *
 * So they are drawers: a panel that opens *over* the conversation rather than
 * replacing it, and which hands back what it holds when it closes. The
 * discipline that made them modes is the part worth keeping, because a drawer
 * that leaks is worse than a mode that leaks — it is a whole inbox open
 * behind whatever the person went on to do. `onLeave` fires for every close,
 * including the one caused by going somewhere else.
 */

import { $ } from './dom.js';

const DRAWERS = ['search', 'mail'];

let current = null;
const listeners = new Set();

export function drawer() {
  return current;
}

export function onDrawerChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function apply() {
  document.body.classList.toggle('drawer-open', current !== null);
  for (const name of DRAWERS) {
    const pane = $(`#drawer-${name}`);
    if (pane) pane.hidden = name !== current;
    const button = document.querySelector(`.nav-row[data-drawer="${name}"]`);
    if (button) button.setAttribute('aria-current', String(name === current));
  }
  for (const fn of listeners) {
    try { fn(current); } catch (err) { console.error(`drawer listener for ${current}:`, err); }
  }
}

export function openDrawer(name) {
  if (!DRAWERS.includes(name) || name === current) return;
  closeDrawer();
  current = name;
  apply();
}

export function closeDrawer() {
  if (current === null) return;
  const was = current;
  current = null;
  apply();
  // Deliberately last: the drawer is already hidden, so a slow teardown cannot
  // leave a visible panel that has stopped working.
  const leave = leaveHooks.get(was);
  if (leave) {
    try { leave(); } catch (err) { console.error(`closing drawer ${was}:`, err); }
  }
}

/* Per-drawer teardown, for the same reason modes had `onLeave`: whatever the
 * drawer opened, the drawer closes. */
const leaveHooks = new Map();

export function onClose(name, fn) {
  leaveHooks.set(name, fn);
}

/* An `Escape` inside a drawer closes the drawer rather than the turn behind
 * it — the same reasoning as the call surface, for the same reason. */
function onDrawerKey() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || current === null) return;
    if (document.querySelector('dialog[open]')) return;
    event.preventDefault();
    closeDrawer();
  });
}

/* Buttons that toggle a drawer, from anywhere. The rail keeps one row per
 * drawer rather than a mode row, because "open my mail" is still a thing a
 * person does with a mouse — but it opens *over* the conversation now, so it
 * cannot be the thing you are lost in. */
export function wireDrawers() {
  for (const button of document.querySelectorAll('.nav-row[data-drawer]')) {
    button.onclick = () => {
      const name = button.dataset.drawer;
      if (name === current) closeDrawer();
      else openDrawer(name);
    };
  }
  for (const name of DRAWERS) {
    for (const button of document.querySelectorAll(`[data-close-drawer="${name}"]`)) {
      button.onclick = () => closeDrawer();
    }
  }
  onDrawerKey();
}