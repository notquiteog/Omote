/* Search results, as something you read in the conversation.
 *
 * This was a drawer: a rail button, a box, and a results list you left the
 * conversation to look at. That was the wrong shape for two reasons, and only
 * one of them was about tidiness.
 *
 * The structural reason is that `web_search` already existed as a tool, so the
 * drawer was a *second* door onto one backend. Two doors is two places for the
 * thing to be wrong: the drawer searched without the model seeing the query and
 * the results, so "look this up" and "here is what I found" were two
 * conversations rather than one. Now the model searches, and you read the hits
 * where they are asked about, which is the conversation you would have had in
 * the first place.
 *
 * The reader is kept, unchanged and for the same reason as before: reading a
 * result without opening a tab, through the same fetch the agent uses — so
 * the private-address guard applies to a person clicking a link, not only to a
 * model following one.
 */

import { el, json } from './dom.js';

/** The results of a `web_search` call, as a card body. */
export function searchCard(display) {
  const box = el('div', 'card-search');
  const hits = display.results || [];
  if (!hits.length) {
    box.appendChild(el('p', 'meta', 'No results.'));
    return box;
  }
  box.appendChild(el('p', 'meta',
    `${display.count ?? hits.length} results via ${display.backend || 'the configured backend'}`));

  for (const hit of hits) {
    const row = el('div', 'hit');
    const title = el('a', 'title', hit.title || hit.url);
    title.href = hit.url;
    title.target = '_blank';
    title.rel = 'noreferrer';
    row.appendChild(title);
    row.appendChild(el('div', 'url', hit.url));
    if (hit.snippet) row.appendChild(el('div', 'snippet', hit.snippet));
    const read = readButton(hit, row);
    row.appendChild(read);
    box.appendChild(row);
  }
  return box;
}

function readButton(hit, row) {
  const read = el('button', 'ghost small read', 'Read it here');
  read.onclick = () => {
    // Toggle in place, under the hit it belongs to. This used to be a panel
    // that a button opened elsewhere, and when the panel went the handler was
    // left building a reader and returning it to nobody — so the button did
    // nothing at all, which is exactly what a browser check is for and a module
    // graph is not.
    const already = row.querySelector('.search-reader');
    if (already) { already.remove(); read.textContent = 'Read it here'; return; }
    row.appendChild(reader(hit.url, hit.title || hit.url));
    read.textContent = 'Hide it';
  };
  return read;
}

/** A page, opened in place inside the tool card. */
function reader(url, title) {
  const box = el('div', 'search-reader');
  const head = el('header');
  head.appendChild(el('span', '', title));
  head.appendChild(el('div', 'spacer'));
  const close = el('button', 'ghost small', 'Close');
  head.appendChild(close);
  const body = el('pre', '', 'fetching…');
  box.append(head, body);
  close.onclick = () => box.remove();

  json(`/api/search/fetch?url=${encodeURIComponent(url)}`).then((data) => {
    body.textContent = data
      ? data.text
      : 'that page could not be fetched. Private and link-local addresses are refused '
        + 'on purpose — a page reached through here can otherwise reach services never '
        + 'exposed to the internet.';
  });
  return box;
}