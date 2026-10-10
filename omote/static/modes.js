/* Which of the two things this is being right now.
 *
 * A body class, and one event. Everything else — which pane is visible, which
 * rail item is current, whether the composer exists — falls out of CSS, so
 * there is no imperative show/hide to get out of step with itself.
 *
 * There were seven of these, and reducing them to two was not a tidying.
 * Every one of the other five was a place the *decision* had been pushed out
 * to, and each push cost something:
 *
 *   - **Talk and Live** were modes because a microphone is a different kind of
 *     attention. They still are — which is why there is a body class and a
 *     rule that gives back the microphone when the call ends. But they are not
 *     *places*, and treating them as places meant a rail of seven buttons where
 *     two of them could only be reached through a third ("the one button that
 *     switches to talk"). They are now a call surface inside Chat, entered by
 *     the two buttons that ought to exist, and left the way you left it.
 *
 *   - **Search and Mail** were modes because "I want to look something up" was
 *     not expressible as a message. It is now: they are tools, and the tools
 *     have always existed. What a mode added on top of a tool was a *second*
 *     way to reach the same store, and two ways to reach one store is two
 *     places for it to be wrong.
 *
 *   - **Autopilot** was a mode because somebody had to start the run. The
 *     harness starts it — that is the entire point of having one — and the run
 *     is a turn like any other, so it belongs in the transcript where you can
 *     read what it did afterwards.
 *
 * What is left is the two things that really are different places: a
 * conversation, and a place to make things.
 *
 * Modes are told when they are left as well as when they are entered, and that
 * is the part that matters: a mode which is left must hand back what it holds.
 */

const MODES = ['chat', 'studio'];
const STORE = 'omote.mode';

const entering = new Map();
const leaving = new Map();

let current = 'chat';

export function onEnter(mode, fn) {
  entering.set(mode, fn);
}

export function onLeave(mode, fn) {
  leaving.set(mode, fn);
}

export function mode() {
  return current;
}

export function go(next) {
  if (!MODES.includes(next) || next === current) return;

  const previous = current;
  current = next;

  const stop = leaving.get(previous);
  if (stop) {
    try {
      stop();
    } catch (err) {
      // A mode that throws on the way out must not trap you in it.
      console.error(`leaving ${previous}:`, err);
    }
  }

  for (const cls of MODES) document.body.classList.toggle(`mode-${cls}`, cls === next);
  for (const button of document.querySelectorAll('.nav-row.mode')) {
    button.setAttribute('aria-current', String(button.dataset.mode === next));
  }
  for (const cls of MODES) {
    const pane = document.getElementById(`pane-${cls}`);
    if (pane) pane.hidden = cls !== next;
  }

  try { localStorage.setItem(STORE, next); } catch { /* private window */ }

  const start = entering.get(next);
  if (start) start();
}

export function wireModes() {
  for (const button of document.querySelectorAll('.nav-row.mode')) {
    button.onclick = () => go(button.dataset.mode);
  }

  let remembered = 'chat';
  try { remembered = localStorage.getItem(STORE) || 'chat'; } catch { /* private window */ }
  // A rename, not a removal: a tab left open across this change has the old
  // name in it, and landing nowhere because of that would be a rude way to
  // find out.
  if (!MODES.includes(remembered)) remembered = 'chat';

  document.body.classList.add('mode-chat');
  go(remembered);
  // `go` is a no-op when the remembered mode is already current, so the
  // initial class and pane state are set explicitly here.
  if (remembered === 'chat') {
    for (const button of document.querySelectorAll('.nav-row.mode')) {
      button.setAttribute('aria-current', String(button.dataset.mode === 'chat'));
    }
  }
}