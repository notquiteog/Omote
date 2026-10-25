/* Keep the conversation's actual controls reachable while its transcript is hidden. */
import { call, onCallChange } from './call.js';

const moved = new Map();
let modeHome = null;

function restore(node, marker) {
  if (marker.parentNode) marker.replaceWith(node);
  else node.remove();
  moved.delete(node);
}

export function restoreCallPrompts() {
  for (const [node, marker] of moved) restore(node, marker);
}

export function wireCallPrompts() {
  const transcript = document.querySelector('#transcript');
  const prompts = document.querySelector('#call-prompts');
  const mode = document.querySelector('#mode-wrap');
  const pending = node => Boolean(node.querySelector('.decide, .opts, form'));
  const sync = () => {
    for (const [node, marker] of moved) {
      if (!call() || !pending(node)) restore(node, marker);
    }
    if (call()) {
      const candidates = new Set([
        ...[...transcript.querySelectorAll('.decide')].map(bar => bar.closest('.tool')),
        ...transcript.querySelectorAll('.question'),
      ]);
      for (const node of candidates) {
        if (!node || !pending(node)) continue;
        const marker = document.createComment('call prompt');
        node.before(marker);
        moved.set(node, marker);
        prompts.append(node);
      }
      if (!modeHome) {
        modeHome = document.createComment('conversation permission control');
        mode.before(modeHome);
        document.querySelector('#call-permissions').append(mode);
      }
    } else if (modeHome) {
      modeHome.replaceWith(mode);
      modeHome = null;
    }
    prompts.hidden = !prompts.children.length;
  };
  const observer = new MutationObserver(sync);
  observer.observe(transcript, {childList: true, subtree: true});
  observer.observe(prompts, {childList: true, subtree: true});
  onCallChange(sync);
  sync();
}
