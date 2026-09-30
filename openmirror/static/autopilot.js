/* Autopilot, as a card in the transcript.
 *
 * It was a mode: a rail button, a goal box, and a live view of a screen. That
 * put the decision of whether a run should happen at all in the hands of
 * whoever clicked, and left the model no part in it — which is the wrong way
 * round for the one capability whose whole value is that the model decides it
 * needs a long unattended run rather than thirty check-ins.
 *
 * Now it is the `autopilot` tool, and this is the card that call produces. The
 * two sockets are still two sockets and still for the same reasons: the
 * narration is small, ordered and replayable to a client that reattaches; the
 * video is large, continuous and worthless five seconds later. On one channel
 * you would either store an hour of stale frames to replay, or lose the
 * narration when someone reloads the page.
 *
 * What changed is what happens when it stops. A mode's live view is worth
 * nothing five minutes later; a tool call is in the transcript, and this card
 * folds itself away and leaves the narration, which is still true.
 */

import { $, api, el, socket } from './dom.js';

let card = null;
let screen = null;
let run = null;
let frameUrl = null;
let hooks = {};

/* The card is built rather than declared in the HTML, because it is made once
 * per *tool call* and there can be several in a transcript — one per run, all
 * of them readable afterwards. A single declared element would be one run, and
 * it would be whichever one arrived last. */
function build(goal, runId) {
  const node = el('div', 'autopilot-card');

  const screenBox = el('div', 'autopilot-screen');
  const frame = document.createElement('img');
  frame.alt = 'what the agent is looking at';
  const offline = el('div', 'autopilot-offline', 'waiting for the first frame…');
  offline.hidden = true;
  screenBox.append(frame, offline);

  const side = el('div', 'autopilot-side');
  const head = el('div', 'autopilot-head');
  const what = el('span', 'what', goal);
  const stage = el('span', 'pill', 'its own screen');
  head.append(what, stage);

  const narration = el('div', 'autopilot-narration');
  narration.setAttribute('role', 'log');
  narration.setAttribute('aria-live', 'polite');

  const foot = el('div', 'autopilot-foot');
  const doing = el('span', 'meta', 'starting…');
  const spacer = el('div', 'spacer');
  const fold = el('button', 'ghost small', 'Hide the screen');
  fold.title = 'The live view is worth nothing once the run is over; the narration is not.';
  const stop = el('button', 'danger small', 'Stop');
  foot.append(doing, spacer, fold, stop);

  side.append(head, narration, foot);
  node.append(screenBox, side);

  // Folded from the start on a narrow window, where a picture and a log beside
  // it leaves neither readable.
  if (window.matchMedia('(max-width: 780px)').matches) node.classList.add('folded');
  fold.onclick = () => {
    node.classList.toggle('folded');
    fold.textContent = node.classList.contains('folded') ? 'Show the screen' : 'Hide the screen';
  };
  stop.onclick = () => stopRun(node, stop);

  return { node, frame, offline, narration, doing, stage, stop };
}

function step(text, kind = '') {
  if (!card) return null;
  const node = el('div', `step ${kind}`, text);
  // The previous "now" stops being now.
  for (const old of card.narration.querySelectorAll('.step.now')) old.classList.remove('now');
  card.narration.appendChild(node);
  card.narration.scrollTop = card.narration.scrollHeight;
  return node;
}

function doing(text) {
  if (card) card.doing.textContent = text;
}

function showFrame(blob) {
  if (!card) return;
  const next = URL.createObjectURL(blob);
  // The previous object URL is revoked only once the new frame has decoded,
  // because revoking the one currently painted makes the image flash white
  // between frames — at one and a half frames a second, that is a strobe.
  card.frame.onload = () => {
    if (frameUrl) URL.revokeObjectURL(frameUrl);
    frameUrl = next;
  };
  card.frame.src = next;
  card.offline.hidden = true;
}

function watchScreen(runId) {
  screen = new WebSocket(socket('/ws/autopilot', { run: runId }));
  screen.binaryType = 'blob';
  screen.onmessage = (msg) => {
    if (msg.data instanceof Blob) showFrame(msg.data);
    else {
      const ev = JSON.parse(msg.data);
      if (ev.type === 'error') step(ev.message, 'bad');
    }
  };
  screen.onclose = () => {
    if (!card) return;
    card.offline.hidden = false;
    card.offline.textContent = 'the screen stream ended';
  };
}

/**
 * A run started. `info` is what the server said about it, so the card can say
 * which screen it got rather than assuming.
 *
 * Returns the element to put the card inside, or null when there is nowhere to
 * put it — a tool card that has not arrived yet is a tool card that is drawn
 * as a line of prose and then replaced, which flickers.
 */
export function attach(info) {
  if (run) return card && card.node;
  run = info;
  card = build(info.goal || 'working', info.run || info.id);
  card.stage.textContent = info.shares_pointer ? 'your screen' : 'its own screen';
  step('Started.');
  watchScreen(info.run || info.id);
  if (hooks.render) hooks.render(card.node);
  return card.node;
}

/* Agent events, rendered as narration rather than as a transcript. Only three
   things are worth a line: what it said, what it is about to do, and anything
   that stopped it. Tool output is deliberately not shown — a build log
   scrolling past is the transcript again, and the transcript is two cards up. */
export function narrate(ev) {
  if (!run || ev.session_id !== run.session_id) return;
  // A subagent's words are its own working, not the run's narration, and
  // spliced in they would garble the sentence the run is in the middle of.
  // Its tool calls still get a line each, like anything else the run does.
  if (ev.agent && ev.type === 'text.delta') return;

  switch (ev.type) {
    case 'text.delta': {
      const box = card.narration.querySelector('.step.said, .step.now');
      const text = (box ? box.textContent : '') + ev.text;
      if (box) box.textContent = text;
      else step(text, 'said');
      break;
    }
    case 'tool.proposed':
      if (ev.needs_approval) step(`Waiting for you: ${ev.call.summary}`, 'stuck');
      else step(ev.call.summary, 'now');
      break;
    case 'question.asked':
      step(ev.question, 'stuck');
      break;
    case 'error':
      step(ev.message, 'bad');
      break;
    case 'turn.completed':
      doing('done');
      foldUp();
      break;
  }
}

/* The run is over, so the picture is stale. Fold it away rather than removing
   the card: the narration is the record, and the card is what says the record
   is there. */
function foldUp() {
  if (!card) return;
  card.node.classList.add('folded');
  if (card.stop) card.stop.disabled = true;
}

async function stopRun(node, button) {
  if (!run) return;
  button.disabled = true;
  await api(`/api/autopilot/${run.id}`, { method: 'DELETE' });
  if (screen) screen.close();
  screen = null;
  step('Stopped.', 'stuck');
  doing('stopped');
  run = null;
  card = null;
  node.classList.add('folded');
}

/** A run this client did not start, so somebody else did. */
export async function reattach() {
  const res = await api('/api/autopilot', { method: 'GET' });
  if (!res || !res.ok) return null;
  const data = await res.json().catch(() => null);
  if (!data || !data.runs || !data.runs.length) return null;
  const [first] = data.runs;
  const node = attach({ ...first, session_id: first.session });
  if (node) step('Reattached to a run that was already going.');
  return node;
}

export function running() {
  return Boolean(run);
}

/**
 * `render(node)` is called with the card, so the caller can put it wherever the
 * transcript puts tool output. Autopilot's narration still comes from the
 * ordinary agent socket rather than a second one: two readers of one event
 * stream is two places for the sequence number to drift.
 */
export function wireAutopilot(handlers = {}) {
  hooks = handlers;
  return $('#autopilot-slot');
}

export function forget() {
  if (screen) screen.close();
  screen = null;
  card = null;
  run = null;
  if (frameUrl) URL.revokeObjectURL(frameUrl);
  frameUrl = null;
}