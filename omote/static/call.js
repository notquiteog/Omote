/* Talking to it, from inside the conversation.
 *
 * Talk and Live were two of the seven modes. Making them places meant the rail
 * had seven buttons, two of which could only be reached *through* a third —
 * a "talk" button in the header that switched mode, opened the microphone and
 * started listening, because a mode you have to configure before you can speak
 * is a mode you use once. And it meant the thing you are talking to was
 * somewhere else, so "what did it say?" was a question about a different place
 * from the one holding the rest of the conversation.
 *
 * They are a call surface now: a state of Chat rather than a place of its own,
 * entered by the two buttons that should have existed, and left the way you
 * left it. The rule that made them modes is kept, because it was the right
 * rule — a call left running because somebody clicked away from it is a
 * microphone left open — and it is enforced here instead:
 *
 *   * starting a call gives back the chat furniture (composer, transcript)
 *     rather than covering it, so there is nothing to type into while the orb
 *     is up. A "just talk to it" surface with a text box on it is a text box.
 *   * `Escape` ends the call, whatever else it would have done.
 *   * switching to Studio ends the call. Going somewhere else and coming back
 *     to a live microphone is a surprise, not a restored state.
 *
 * Live's honesty note is deliberately kept even though the pane is gone: it is
 * OpenAI's model, it cannot run on your own hardware, and everything said in
 * the room goes to them while it is open. Ordinary Talk routes dictation,
 * thinking and speech separately, so each of those can stay on your machine.
 * That is the difference worth knowing before pressing the other button.
 */

import { $ } from './dom.js';
import { endLive, goLive, isLive } from './live.js';
import { startTalking, stopTalking, talking } from './talk.js';

const KINDS = ['talk', 'live'];

let current = null;
const listeners = new Set();

/** Which call is open, or null. */
export function call() {
  return current;
}

export function onCallChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function apply() {
  const surface = $('#call-surface');
  if (surface) surface.hidden = current === null;
  // `calling` is what the stylesheet hangs the swap on; the specific class is
  // what picks which of the two stages is showing.
  document.body.classList.toggle('calling', current !== null);
  for (const kind of KINDS) {
    document.body.classList.toggle(`call-${kind}`, current === kind);
    const stage = $(`#call-${kind}`);
    if (stage) stage.hidden = current !== kind;
  }
  for (const fn of listeners) {
    try { fn(current); } catch (err) { console.error(`call listener for ${current}:`, err); }
  }
}

/**
 * One button, and it does the whole thing.
 *
 * This is the button that used to be in the header and had to switch mode
 * first. It is the same single control as before — press it and you are
 * talking, press it again and you are not — with the mode indirection taken
 * out, because the indirection was the only thing standing between the button
 * and the microphone.
 */
export async function toggleTalk(options = {}) {
  if (current === 'talk') {
    await endCall();
    return;
  }
  await startCall('talk', options);
}

export async function toggleLive(options = {}) {
  if (current === 'live') {
    await endCall();
    return;
  }
  await startCall('live', options);
}

export async function startCall(kind, options = {}) {
  if (!KINDS.includes(kind)) return;
  // Starting one kind while another is open ends the other first, rather than
  // leaving two microphones and two sockets to answer to.
  if (current && current !== kind) await endCall();
  current = kind;
  apply();
  if (kind === 'talk') await startTalking(options);
  else await goLive(options);
}

export async function endCall() {
  const was = current;
  current = null;
  apply();
  if (was === 'live') endLive();
  else stopTalking();
}

/* The bar's two buttons, and `Escape`.
 *
 * `Escape` is handled here rather than in the caller's keydown chain, because
 * ending a call is the only thing that has to happen first and in every case:
 * the old chain was a priority list over five conditions and this is the one
 * that owns a resource. Stopping it wins. */
export function wireCalls(options = {}) {
  const talk = $('#talk-button');
  if (talk) talk.onclick = () => toggleTalk(options);
  const live = $('#live-button');
  if (live) live.onclick = () => toggleLive(options);

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (current === null) return;
    // Never swallow the key from a dialog that is open over the call: Escape
    // closes a dialog, and a call underneath it is not what the person meant.
    if (document.querySelector('dialog[open]')) return;
    event.preventDefault();
    endCall();
  });
}

/* What the composer needs to know, so the two surfaces can hand back to each
 * other. Exported rather than reimplemented, because `talking()` and
 * `isLive()` answer "is a socket open", which is not the same question as "is
 * the call surface up" — a call whose socket died still has to be put away. */
export const busy = () => talking() || isLive();