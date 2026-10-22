import asyncio
import os
import shutil
import time

import pytest

from omote.agent.browser import BrowserConfig, BrowserSession
from omote.agent.browsers import BrowserChoice

pytest.importorskip('playwright.async_api')


async def test_shared_login_private_tabs_and_restart(tmp_path):
    def browser():
        return BrowserSession(BrowserConfig(profile_dir=tmp_path / 'profile', choice=BrowserChoice(engine=os.getenv('OMOTE_TEST_BROWSER', 'chromium')), headless=True))

    first, second = browser(), browser()
    try:
        a, b = await asyncio.gather(first.page(), second.page())
        assert a is not b and first._context is second._context
        # No real account or external service: simulate a site's persistent login.
        await first._context.add_cookies(
            [
                {
                    'name': 'login',
                    'value': 'local-test-session',
                    'domain': 'shop.test',
                    'path': '/',
                    'expires': time.time() + 86400,
                }
            ]
        )
        await first._context.route(
            'https://shop.test/**',
            lambda route: route.fulfill(body='<button>Post listing</button>', content_type='text/html'),
        )
        await a.goto('https://shop.test/listing')
        await b.goto('https://shop.test/inbox')
        assert await b.evaluate('document.cookie') == 'login=local-test-session'
        await first.elements()
        assert first.last_elements and not second.last_elements
        await b.goto('https://shop.test/another-conversation')
        assert a.url == 'https://shop.test/listing'
        await first.close()
        assert not b.is_closed()
        await second.close()
        third = browser()
        try:
            c = await third.page()
            assert c.url == 'about:blank', 'A new conversation must never inherit another tab’s history.'
            assert any(
                cookie['value'] == 'local-test-session' for cookie in await third._context.cookies('https://shop.test/')
            )
        finally:
            await third.close()
    finally:
        await first.close()
        await second.close()


async def test_conflicting_browser_settings_do_not_create_throwaway_login(tmp_path):
    choice = BrowserChoice(engine=os.getenv('OMOTE_TEST_BROWSER', 'chromium'))
    first = BrowserSession(BrowserConfig(profile_dir=tmp_path / 'profile', choice=choice, headless=True))
    second = BrowserSession(BrowserConfig(profile_dir=tmp_path / 'profile', choice=choice, headless=False))
    try:
        await first.page()
        with pytest.raises(RuntimeError, match='login has been kept'):
            await second.page()
        assert second._context is None
    finally:
        await first.close()
        await second.close()


async def test_visible_login_can_be_reused_by_a_later_background_chat(tmp_path):
    if not shutil.which('Xvfb'):
        pytest.skip('This visible sign-in test uses an isolated Xvfb display.')
    from omote.agent.stage import VirtualStage
    stage = VirtualStage(1024, 768)
    choice = BrowserChoice(engine=os.getenv('OMOTE_TEST_BROWSER', 'chromium'))
    options = dict(profile_dir=tmp_path / 'login-profile', choice=choice, env=stage.env())
    first = BrowserSession(BrowserConfig(**options))
    later = BrowserSession(BrowserConfig(**options))
    try:
        await first.page()
        await first._context.add_cookies([{'name': 'saved-login', 'value': 'test', 'domain': 'shop.test', 'path': '/', 'expires': time.time() + 3600}])
        visible = await first.show_for_login()
        assert not first._entry['headless']
        new_page = await later.page()
        assert new_page is not visible
        assert first._context is later._context
        assert any(c['name'] == 'saved-login' for c in await later._context.cookies('https://shop.test/'))
    finally:
        await first.close()
        await later.close()
        stage.close()
