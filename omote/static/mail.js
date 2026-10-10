/* Mail, as something you read in the conversation.
 *
 * This was a drawer, and that was the wrong shape. The `mail` tool already
 * existed, so the drawer was a *second* door onto one IMAP connection — and two
 * doors is two places for the thing to be wrong. It also meant the two doors
 * could not see each other: the drawer read your inbox without the model
 * knowing you had, so "what came in today" was something you did and "what came
 * in today" was something you asked, and the second conversation could not build
 * on the first.
 *
 * Now there is one door and it is the tool. The model reads, searches, drafts
 * and replies; the results arrive here and are drawn into the tool card, in
 * the place they were asked about.
 *
 * Everything this file used to fetch over its own HTTP calls, it no longer
 * fetches at all: the data is already in the tool's `display` payload, because
 * the tool is what went and got it. That is the whole of the change — the
 * renderer is now a function of the tool's result rather than a second client
 * with its own view of the server.
 *
 * Two decisions carried over unchanged:
 *
 *   - **Nothing is sent from here.** There is no send button. The model
 *     drafts, you read the draft in the card, and it goes by the approval the
 *     tool already grades. A drawer with a send button is a second, quieter way
 *     for a message to leave without anyone reading it.
 *   - **A listing does not mark anything read.** That is the server's
 *     `BODY.PEEK`, and it is why expanding a list here cannot clear your unread
 *     badge behind your back.
 */

import { el } from './dom.js';

/** One `mail` tool result, as a card body. */
export function mailCard(display) {
  const box = el('div', 'card-mail');

  if (display.unconfigured) {
    box.appendChild(el('p', 'meta', 'No mail account on this install.'));
    return box;
  }
  if (display.accounts) return accounts(box, display.accounts);
  if (display.folders) return folders(box, display.folders);
  if (display.mails) return listing(box, display);
  if (display.mail) return one(box, display.mail);
  if (display.thread) return reply(box, display);
  if (display.drafted) return drafted(box, display);
  return box;
}

function accounts(box, found) {
  if (!found.length) {
    box.appendChild(el('p', 'meta', 'No mail account is configured.'));
    return box;
  }
  for (const one of found) {
    const row = el('div', 'mail-account');
    row.appendChild(el('span', 'mail-from', one.label || one.address));
    const where = [one.protocol, one.imap_host, one.address].filter(Boolean).join(' · ');
    row.appendChild(el('span', 'meta', where));
    box.appendChild(row);
  }
  return box;
}

function folders(box, found) {
  const row = el('div', 'mail-folders');
  for (const one of found) {
    const chip = el('span', 'chip flat', one.name || String(one));
    if (one.unseen) chip.classList.add('unseen');
    row.appendChild(chip);
  }
  box.appendChild(row);
  return box;
}

function listing(box, display) {
  const where = display.search
    ? `matching ${JSON.stringify(display.search)} in ${display.folder}`
    : `in ${display.folder}`;
  box.appendChild(el('p', 'meta', `${display.mails.length} message(s) ${where}`));
  for (const message of display.mails) box.appendChild(row(message));
  return box;
}

function one(box, message) {
  const wrap = el('div', 'mail-one');
  wrap.appendChild(head(message));
  wrap.appendChild(el('div', 'meta', `to ${recipients(message.to)}${message.cc?.length ? ` · cc ${recipients(message.cc)}` : ''} · ${message.date || 'no date'}`));
  wrap.appendChild(el('pre', 'mail-body', message.body || message.snippet || '(empty)'));
  box.appendChild(wrap);
  return box;
}

function reply(box, display) {
  // What a reply call sent. Shown as a record, not an invitation to send again:
  // there is no send button here and there is not going to be one.
  box.appendChild(el('p', 'meta', `Replied to the thread${display.folder ? ` in ${display.folder}` : ''}.`));
  if (display.subject) box.appendChild(el('div', 'mail-subject', display.subject));
  return box;
}

function drafted(box, display) {
  box.appendChild(el('p', 'meta', `Drafted${display.folder ? ` in ${display.folder}` : ''}. It has not been sent.`));
  if (display.subject) box.appendChild(el('div', 'mail-subject', display.subject));
  return box;
}

/** A message in a list: a subject you can open, not a message that opens itself.

    Reading is a separate call on purpose — `mail read` is a real fetch, and it
    is what marks a message seen. Clicking a row here shows the body the tool
    already fetched and nothing more, so expanding a list never changes what
    your mailbox thinks you have read.
    */
function row(message) {
  const node = el('div', 'mail-row');
  const subject = el('div', 'mail-subject', message.subject || '(no subject)');
  if (!message.seen) subject.classList.add('unseen');
  node.appendChild(subject);
  node.appendChild(el('div', 'meta', [
    message.from?.name || message.from?.email || '',
    message.date || '',
    message.answered ? '· replied' : '',
  ].filter(Boolean).join(' · ')));
  if (message.snippet) node.appendChild(el('div', 'snippet', message.snippet));

  const open = el('button', 'ghost small', 'Read it');
  open.onclick = () => {
    const already = node.querySelector('.mail-body');
    if (already) { already.remove(); open.textContent = 'Read it'; return; }
    const body = el('pre', 'mail-body', message.body || message.snippet || '(empty)');
    node.appendChild(body);
    open.textContent = 'Hide it';
  };
  node.appendChild(open);
  return node;
}

function head(message) {
  const node = el('header');
  const subject = el('h2', 'mail-subject', message.subject || '(no subject)');
  node.appendChild(subject);
  return node;
}

function recipients(people) {
  const list = people || [];
  if (!list.length) return 'nobody';
  if (list.length <= 3) return list.map((p) => p.name || p.email).join(', ');
  return `${list.slice(0, 3).map((p) => p.name || p.email).join(', ')} and ${list.length - 3} more`;
}