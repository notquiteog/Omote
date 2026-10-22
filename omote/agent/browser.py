"""A real browser the agent can drive.

Three decisions worth stating.

**The profile is persistent.** Shopping, or anything else on a site you have
an account with, is impossible from a fresh incognito context — you would be
logged out every time. So the profile lives on disk and the agent inherits
whatever sessions are in it. That is also the safest arrangement for
credentials: **you** log in once, by hand, and the agent never sees a password
because it never needs one.

**Actions are by element reference, not coordinates.** `read_page` numbers the
interactive elements and hands back a list; the click tool takes one of those
numbers. Coordinates go stale the moment a page reflows, an ad loads or a
banner appears, and a stale coordinate does not fail — it clicks whatever is
there now, which on a checkout page is exactly the wrong failure mode.

**One page per agent session.** Compatible sessions share the persistent
browser login, while navigation, page history and element references stay on
separate tabs. A busy or incompatible profile is reported, never replaced by
a throwaway profile that silently loses the user's login.
"""

from __future__ import annotations

import asyncio
import logging
import os
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Event-loop local: pytest and embedded clients may run several independent
# loops. A lock/context created on one loop must never be reused on another.
_POOLS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _pool():
    loop = asyncio.get_running_loop()
    if loop not in _POOLS:
        _POOLS[loop] = (asyncio.Lock(), {})
    return _POOLS[loop]

# What a page hands back, after being asked what is on it.
INTERACTIVE_JS = """
() => {
  const out = [];
  const seen = new Set();
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=tab], [onclick], [contenteditable=true]';
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    // Off-screen or zero-size elements are not things a person could click,
    // and offering them to the model invites it to try.
    if (r.width < 2 || r.height < 2) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;

    // Whether this field holds a secret, decided *here*, before the value
    // crosses out of the page. It duplicates `_SECRET_NAME` in
    // tools/browser.py and that duplication is deliberate: the Python side is
    // the authority on how a call is *graded*, but by the time it sees this
    // list the value has already been read out of the browser, put in a
    // result and handed to a model. The only place a secret can be kept out
    // of all three is on this side of the boundary.
    const secretish = /(pass(word|wd|phrase)?|pwd|otp|mfa|2fa|totp|one[-_ ]?time|card[-_ ]?(number|num)?|ccnum|cc[-_ ]?number|credit[-_ ]?card|cvv|cvc|csc|security[-_ ]?code|verification[-_ ]?code|ssn|social[-_ ]?security|passport|pin|secret|api[-_ ]?key|token|private[-_ ]?key|seed[-_ ]?phrase|mnemonic|recovery[-_ ]?phrase)/i;
    const associated = Array.from(el.labels || [], label => label.innerText).join(' ');
    const naming = [el.getAttribute('name'), el.id, el.getAttribute('placeholder'),
                    el.getAttribute('aria-label'), associated].filter(Boolean).join(' ').normalize('NFKC');
    const regionalSecret = /パスワード|暗証番号|認証コード|確認コード|カード番号|マイナンバー|密码|密碼|验证码|驗證碼|银行卡号|銀行卡號|信用卡号|信用卡號|身份证|身份證|비밀번호|인증번호|카드번호|รหัสผ่าน|mot de passe|code de sécurité|numéro de carte|passwort|kennwort|kartennummer|contraseña|senha|número do cartão|codice fiscale|пароль|номер карты|كلمة المرور/i;
    const autocomplete = el.getAttribute('autocomplete') || '';
    const isSecret = el.type === 'password' || secretish.test(naming) || regionalSecret.test(naming) ||
      autocomplete.split(/\\s+/).some(value => /^(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp|cc-exp-month|cc-exp-year)$/.test(value));

    const label = (
      el.getAttribute('aria-label') ||
      associated ||
      el.innerText ||
      // Never the value on a secret field. This fallback was how a typed
      // password came back out: with no aria-label and no inner text, the
      // element's *label* became whatever had been typed into it, and the
      // separate redaction of `value` below did nothing to stop it.
      (isSecret ? '' : el.value) ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('name') ||
      ''
    ).trim().replace(/\\s+/g, ' ').slice(0, 120);

    const key = el.tagName + '|' + label + '|' + Math.round(r.top) + '|' + Math.round(r.left);
    if (seen.has(key)) continue;
    seen.add(key);

    n += 1;
    el.setAttribute('data-omote-ref', String(n));
    out.push({
      ref: n,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      name: el.getAttribute('name') || '',
      id: el.id || '',
      role: el.getAttribute('role') || '',
      autocomplete,
      contenteditable: el.isContentEditable,
      submit_label: el.form ? Array.from(el.form.querySelectorAll('button:not([type]),button[type=submit],input[type=submit]'),
        button => button.innerText || button.value || button.getAttribute('aria-label') || '').join(' ').slice(0, 500) : '',
      options: !isSecret && el.tagName === 'SELECT' ? Array.from(el.options, option => ({value: option.value.slice(0, 120), label: option.label.slice(0, 120)})).slice(0, 100) : undefined,
      label,
      href: el.getAttribute('href') || '',
      // Redacted by what the field *is*, not by its input type: a card
      // number is `type=text` and was coming back in full.
      value: (isSecret ? '' : (el.value || '')).slice(0, 60),
      secret: isSecret,
      checked: el.checked === true,
      disabled: el.disabled === true,
      accept: el.type === 'file' ? el.accept : undefined,
      multiple: el.type === 'file' ? el.multiple : undefined,
      inViewport: r.top >= 0 && r.top < innerHeight,
    });
    if (n >= 300) break;
  }
  return out;
}
"""


@dataclass
class BrowserConfig:
    # A real profile directory. Log in by hand once; the agent inherits it and
    # never handles a password.
    profile_dir: Path = field(default_factory=lambda: Path.home() / '.omote' / 'browser')
    headless: bool = True
    # Which browser to launch. None means "whatever this install is set to",
    # read at launch time rather than at construction: a session built this
    # morning and first used this afternoon should use the browser chosen in
    # between, not the one that was current when nobody had opened a page yet.
    choice: Any = None
    # Headful is the honest default for anything transactional: you can watch
    # what it is doing and take the mouse off it.
    viewport: tuple[int, int] = (1280, 900)
    timeout_ms: int = 30_000
    user_agent: str | None = None
    # Environment for the browser process — in practice a DISPLAY, which is
    # what puts a real headful Chromium onto the agent's own X server rather
    # than onto the screen the person is using. A visible browser on a display
    # nobody is looking at is the best of both: the agent can see it with the
    # desktop tools, and it cannot steal focus from anyone.
    env: dict[str, str] = field(default_factory=dict)


class BrowserSession:
    """Owns one private tab, with a reference-counted persistent browser."""

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        # This means persistent profile, not proof a particular site is logged in.
        self.signed_in_profile = True
        self._entry: Any = None
        # What the last read saw, keyed by ref. The click and type tools grade
        # their risk from this without awaiting, which they must: `assess` is
        # synchronous by design, so that an approval decision cannot itself
        # touch the page it is deciding about.
        self.last_elements: dict[int, dict[str, Any]] = {}

    async def page(self) -> Any:
        """The current page, starting the browser on first use.

        Lazy because most sessions never touch a browser, and paying 300 ms and
        200 MB for one that is not used is a poor trade.
        """
        async with self._lock:
            if self._entry and self._entry.get('closed'):
                self._pw = self._context = self._page = self._entry = None
                self.last_elements.clear()
            if self._page is not None and not self._page.is_closed():
                return self._page

            if self._context is None:
                await self._acquire()

            # Never reuse another conversation's page or a restored old tab.
            self._page = await self._context.new_page()
            await self._page.set_viewport_size({'width': self.config.viewport[0], 'height': self.config.viewport[1]})
            self._page.set_default_timeout(self.config.timeout_ms)
            self.last_elements.clear()

            # A new page means the old refs are meaningless. Clearing here
            # rather than trusting callers means a stale ref grades as unknown
            # — which escalates — instead of inheriting the old element's
            # harmless-looking label.
            owned_page = self._page
            owned_page.on('framenavigated', lambda frame: self.last_elements.clear()
                          if self._page is owned_page and frame == owned_page.main_frame else None)
            return self._page

    async def _acquire(self):
        from omote.agent.browsers import current
        from omote.setup.browser import activate

        choice = self.config.choice or current()
        key = str(self.config.profile_dir.resolve())
        signature = (choice.engine, repr(choice.launch_kwargs()),
                     self.config.user_agent, tuple(sorted(self.config.env.items())))
        lock, entries = _pool()
        async with lock:
            entry = entries.get(key)
            if entry and entry.get('closed'):
                await entry['pw'].stop()
                entries.pop(key, None)
                entry = None
            if entry:
                if entry['signature'] != signature or (not self.config.headless and entry['headless']):
                    raise RuntimeError('The saved browser login is in use with different browser/display settings. Close that browser chat before continuing; your login has been kept.')
                entry['users'] += 1
            else:
                directory = await self._profile_dir()
                activate()
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                launch = {'headless': self.config.headless, 'user_agent': self.config.user_agent,
                          'user_data_dir': str(directory), **choice.launch_kwargs()}
                if self.config.env:
                    launch['env'] = {key: value for key, value in {**os.environ, **self.config.env}.items() if value != ''}
                    if self.config.env.get('DISPLAY') and choice.engine == 'chromium':
                        launch['args'] = ['--ozone-platform=x11']
                try:
                    context = await getattr(pw, choice.engine).launch_persistent_context(**launch)
                except BaseException:
                    await pw.stop()
                    raise
                entry = {'context': context, 'pw': pw, 'users': 1, 'signature': signature, 'key': key,
                         'headless': self.config.headless, 'closed': False}
                context.on('close', lambda: entry.update(closed=True))
                entries[key] = entry
            self._entry = entry
            self._context, self._pw = entry['context'], entry['pw']

    async def _profile_dir(self) -> Path:
        """Refuse an externally held profile instead of discarding its login."""

        shared = self.config.profile_dir
        shared.mkdir(parents=True, exist_ok=True)

        # `SingletonLock` is a symlink Chromium leaves while a profile is in
        # use, and removes on a clean exit. A stale one after a crash points
        # at a pid that is gone, so it is checked rather than trusted.
        lock = shared / 'SingletonLock'
        if _profile_is_busy(lock):
            raise RuntimeError('The saved browser profile is open in another process. Close that browser and retry; Omote will keep your login.')
        return shared

    async def elements(self) -> list[dict[str, Any]]:
        page = await self.page()
        try:
            found = await page.evaluate(INTERACTIVE_JS)
        except Exception:  # noqa: BLE001 - a navigation mid-evaluate is normal
            await page.wait_for_timeout(500)
            found = await page.evaluate(INTERACTIVE_JS)
        self.last_elements = {e['ref']: e for e in found}
        return found

    async def find(self, ref: int) -> Any:
        """The handle for a ref from the last read_page."""
        page = await self.page()
        locator = page.locator(f'[data-omote-ref="{ref}"]')
        if await locator.count() == 0:
            raise LookupError(
                f'no element {ref} on this page — the page has changed since you read it. '
                'Read it again and use a fresh reference.'
            )
        return locator.first

    async def close(self) -> None:
        async with self._lock:
            lock, entries = _pool()
            async with lock:
                entry = self._entry
                try:
                    if self._page is not None and not self._page.is_closed():
                        await self._page.close()
                finally:
                    self.last_elements.clear()
                    self._pw = self._context = self._page = self._entry = None
                    if entry:
                        entry['users'] -= 1
                        if entry['users'] == 0:
                            if entries.get(entry['key']) is entry:
                                entries.pop(entry['key'])
                            try:
                                await entry['context'].close()
                            finally:
                                await entry['pw'].stop()

    async def show_for_login(self):
        """Make a headless page visible without exporting cookies or passwords."""
        page = await self.page()
        if self._entry and not self._entry['headless']:
            await page.bring_to_front()
            return page
        if self._entry and self._entry['users'] > 1:
            raise RuntimeError('Other chats are using this browser. Close their browser sessions before opening a visible sign-in window.')
        url = page.url
        await self.close()  # Flush persistent cookies before relaunching.
        self.config.headless = False
        try:
            page = await self.page()
            if url != 'about:blank':
                await page.goto(url, wait_until='domcontentloaded')
            await page.bring_to_front()
            return page
        except Exception:
            self.config.headless = True
            await self.close()
            raise RuntimeError('Could not open a visible sign-in window. A desktop display is required; your saved browser login is still on disk.') from None


def _profile_is_busy(lock: Path) -> bool:
    """Whether a Chromium is currently holding this profile.

    The lock is a symlink whose target is `<host>-<pid>`. A crashed Chromium
    leaves one behind pointing at a pid that no longer exists, and treating
    that as busy would mean every session after a crash silently loses its
    logins — so the pid is checked.
    """
    try:
        target = os.readlink(lock)
    except OSError:
        return False

    pid = target.rsplit('-', 1)[-1]
    if not pid.isdigit():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Somebody else's process, which is still a process holding it.
        return True
    return True
