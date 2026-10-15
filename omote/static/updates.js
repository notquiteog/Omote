/* Updates, in Settings.
 *
 * The three steps are three buttons on purpose, and the order is the argument:
 *
 *   1. **Check** — asks GitHub what exists. Free, repeatable, and the only step
 *      that happens on its own (once a day, and never while a turn is running).
 *   2. **Download** — fetches the installer and checks it against the SHA256
 *      published in the same release. Staged as a file; nothing is run.
 *   3. The app offers to install it, or the message says what to type.
 *
 * A single "Update now" button would be one click instead of three, and
 * somebody would press it without reading the release notes, and then never
 * read anything again. The release notes are the only place a breaking change
 * is ever going to be written down.
 *
 * "Not now" clears the notice until the next check finds the same release
 * still newer than this one — which is what somebody who chose not to update
 * wants, and is not the same as being told about it and ignored forever.
 */

import { $, el, json, send } from './dom.js';
import { installUpdate, isDesktop } from './native.js';

const state = { last: null, poll: null, dismissing: false, installing: false, operationError: '' };

function ago(stamp) {
  if (!stamp) return '';
  const then = new Date(stamp);
  if (Number.isNaN(then.getTime())) return '';
  const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (seconds < 90) return 'just now';
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

export function renderUpdate(info) {
  if (!info) return;
  state.last = info;

  $('#update-current').textContent =
    `You are on ${info.current} · ${info.platform}`
    + (info.managed ? '' : ' · running from source, so an update is a git pull');

  const box = $('#update-state');
  box.textContent = '';

  const error = state.operationError || info.error || (info.installation?.status === 'error' ? info.installation.message : '');
  if (error) box.appendChild(el('p', 'update-error', error));
  if (info.installation?.status === 'complete') box.appendChild(el('p', 'meta', info.installation.message));

  if (info.available && info.latest) {
    const release = info.latest;
    box.appendChild(el('p', 'update-available', `${release.version} is available.`));
    box.appendChild(el('p', 'meta', [
      release.name && release.name !== release.version ? release.name : '',
      release.published ? `published ${ago(release.published)}` : '',
      release.prerelease ? 'a preview, not a final release' : '',
    ].filter(Boolean).join(' · ')));

    const notes = $('#update-notes');
    notes.hidden = false;
    notes.textContent = '';
    notes.appendChild(el('pre', 'update-notes-body', release.notes || 'No notes were published.'));
    if (release.notes_truncated) {
      notes.appendChild(el('p', 'meta',
        release.url ? 'The full notes are on the release page.' : ''));
    }
    if (release.url) {
      const link = el('a', 'update-link', 'Release notes on GitHub');
      link.href = release.url;
      link.target = '_blank';
      link.rel = 'noreferrer';
      notes.appendChild(link);
    }
    if (!release.asset) {
      box.appendChild(el('p', 'update-error',
        `That release has no installer for ${info.platform}. The notes below still apply if you ` +
        'build from source.'));
    }
  } else if (!info.error) {
    box.appendChild(el('p', 'meta', info.checked_at
      ? `Up to date${info.latest ? '' : ', and nothing newer has been published'}. `
        + `Last checked ${ago(info.checked_at)}.`
      : 'Not checked yet.'));
  }

  $('#update-dot').hidden = !info.available;
  $('#update-dismiss').hidden = !info.available || state.dismissing;
  $('#update-apply').hidden = !(info.available && info.latest && info.latest.asset);
  $('#update-apply').textContent = info.staged ? 'Install' : 'Download';
  /* `staged` used to disable this permanently, which made the "Install"
     label a button that could never be pressed: the only handler was
     `download()`, which needs a fresh `/apply` that the disabled button could
     no longer ask for. So a staged download on a browser install — where
     nothing runs it for you, because `installUpdate` is a no-op off the
     desktop — was a dead end. The staged file stays where it is and the
     button stays live; what is disabled is only the work in progress. */
  $('#update-apply').disabled = Boolean(info.downloading || state.installing);
  $('#update-check').disabled = Boolean(info.downloading || state.installing);
  $('#update-check').textContent = info.downloading ? 'Working…' : 'Check now';
  $('#update-current').title = info.enabled
    ? ''
    : 'Update checks are off on this install.';

  /* A staged installer the browser cannot run itself needs the command to
     type. On the desktop the app runs it and there is nothing to say. */
  const hint = $('#update-staged');
  if (hint) {
    const staged = info.staged && info.latest && !info.downloading;
    hint.hidden = !staged;
    if (staged) {
      hint.textContent = isDesktop
        ? `On disk at ${info.staged}.`
        : `On disk at ${info.staged}. Move to ${info.latest.version} with:`;
    }
  }
}

function setProgress(info) {
  const wrap = $('#update-progress-wrap');
  if (!info.downloading && !info.staged) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  $('#update-progress-fill').style.width = `${info.progress || 0}%`;
  const mb = (info.downloaded || 0) / (1024 * 1024);
  $('#update-progress-text').textContent = info.downloading
    ? `downloading — ${mb.toFixed(1)}MB`
    : `downloaded — ${mb.toFixed(1)}MB`;
}

async function check(force = true) {
  state.operationError = '';
  renderUpdate(state.last);
  /* `send`, not `post`, and that distinction was the whole feature being
     broken. `post` resolves with a `Response`, which has no `.data` — so
     `body` was always `{}`, the new release and its notes and its error were
     all discarded, and "Check now" appeared to do nothing. `download()` had the
     same bug with a worse outcome: `body.path` was always falsy, so a
     successful, checksum-verified download was reported as "it did not
     download" and the install path was never reached at all.
     `send` returns `{status, data, detail}`, which is what both of these want. */
  const got = await send('/api/update/check', { force, prerelease: false });
  const body = got.data || {};
  renderUpdate({ ...(state.last || {}), ...body, error: body.error || got.detail || '' });
  /* `body.deferred` is the server saying "something is running, ask again" —
     and it belongs with the other messages rather than in a `#update-error`
     element that has never existed, which is why it could never be shown even
     once the body was read correctly. */
  if (body.deferred) $('#update-state')?.append(el('p', 'meta', body.deferred));
}

/* One button, two jobs, and which one is decided by whether a file is already
   on disk.

   They used to be conflated into a button that disabled itself once staged —
   "Install", permanently unpressable, with `download()` as its only handler and
   no way to trigger one. Split, and each half is the thing its label says. */
async function install() {
  const path = state.last?.staged;
  if (!path) return download();
  if (state.installing) return;
  state.operationError = '';
  state.installing = true;
  renderUpdate(state.last);
  const said = await installUpdate(path);
  state.installing = false;
  const failed = String(said).startsWith('saved,');
  if (failed) state.operationError = String(said);
  renderUpdate(state.last);
  const box = $('#update-state');
  if (!failed) {
    box.appendChild(el('p', 'meta', isDesktop ? said : `The verified file is at ${path}. Open it with your system installer.`));
  }
}

async function download() {
  state.operationError = '';
  $('#update-apply').disabled = true;
  $('#update-apply').textContent = 'Downloading…';
  const res = await send('/api/update/apply', {}, { timeout: 600_000 });
  const body = res.data || {};
  if (body.path) {
    const info = { ...(state.last || {}), staged: body.path, downloading: false,
      downloaded: body.bytes, progress: 100 };
    renderUpdate(info);
    setProgress(info);
    $('#update-state').append(el('p', 'meta', body.verified
      ? 'Downloaded and checked against the published SHA-256. Choose Install when ready.'
      : 'Downloaded without published checksums. Open the release page for manual installation.'));
  } else {
    state.operationError = body.detail || res.detail || 'The download did not finish. Try again.';
    renderUpdate({ ...(state.last || {}), downloading: false });
  }
}

async function dismiss() {
  state.dismissing = true;
  await send('/api/update/dismiss', {});
  state.dismissing = false;
  if (state.last) renderUpdate({ ...state.last, available: false, latest: null });
}

export function wireUpdates() {
  $('#update-check').onclick = () => check(true);
  $('#update-apply').onclick = install;
  $('#update-dismiss').onclick = dismiss;
  json('/api/update/status').then((info) => {
    if (info) renderUpdate(info);
  });
}

/* Polled while Settings is open and nowhere else.
 *
 * The reason it stops is the reason the whole feature is polite: a download
 * progress bar that updates itself in a dialog nobody is looking at is a
 * daemon doing work for nobody, and this project's rule is that a mode which
 * is left must hand back what it holds. */
export function watchUpdates(on) {
  clearInterval(state.poll);
  state.poll = null;
  if (!on) return;
  state.poll = setInterval(async () => {
    const info = await json('/api/update/status');
    if (!info) return;
    if (state.last) {
      const wasDownloading = state.last.downloading;
      renderUpdate(info);
      if (wasDownloading) setProgress(info);
    }
  }, 1200);
}
