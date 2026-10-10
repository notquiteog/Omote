"""Companion menus must stay above the workspace and usable on small screens."""

import re

import pytest

from tests import test_workspace_browser

workspace = test_workspace_browser.workspace
playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect


@pytest.mark.parametrize('width,height,scheme', [(1280, 900, 'dark'), (1280, 900, 'light'), (390, 844, 'dark'), (320, 568, 'light')])
def test_companion_picker_layering_selection_and_keyboard(workspace, width, height, scheme):
    page, _ = workspace
    page.set_viewport_size({'width': width, 'height': height})
    page.emulate_media(color_scheme=scheme)
    if width <= 760:
        page.locator('#sidebar-toggle').click()
    button = page.locator('#brand-pet .pet')
    menu = page.locator('#brand-pet .companion-menu')
    button.click()
    expect(menu).to_be_visible()
    assert menu.evaluate("node => node.matches(':popover-open')")
    box = menu.bounding_box()
    assert box['x'] >= 0 and box['y'] >= 0
    assert box['x'] + box['width'] <= width
    assert box['y'] + box['height'] <= height
    # A high child z-index used to leave the preview underneath the composer.
    preview = menu.locator('.companion-preview')
    preview.scroll_into_view_if_needed()
    assert preview.evaluate("""node => {
        const r = node.getBoundingClientRect();
        return node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    }""")
    menu.screenshot(path=f'/tmp/omote-companion-fixed-{width}-{scheme}.png')
    menu.get_by_label('Companion motion').select_option('still')
    assert page.evaluate("localStorage.getItem('omote.companion.motion')") == 'still'
    search = menu.get_by_role('searchbox')
    search.fill('fox')
    expect(menu.locator('.row:visible')).to_have_count(1)
    search.press('ArrowDown')
    page.keyboard.press('Enter')
    expect(menu).to_be_hidden()
    expect(button).to_be_focused()
    assert page.evaluate("localStorage.getItem('omote.companion')") == 'fox'
    button.click()
    menu.get_by_role('searchbox').fill('nobody')
    menu.locator('.row:visible').click()
    expect(page.locator('body')).to_have_class(re.compile(r'.*companion-off.*'))
    button.click()
    page.evaluate("""() => {
        window.workspaceEscapes = 0;
        document.addEventListener('keydown', e => { if (e.key === 'Escape') window.workspaceEscapes++; });
    }""")
    page.keyboard.press('Escape')
    assert page.evaluate('window.workspaceEscapes') == 0
    expect(menu).to_be_hidden()
    expect(button).to_be_focused()
    if width <= 760:
        expect(page.locator('#sidebar-toggle')).to_have_attribute('aria-expanded', 'true')
        page.keyboard.press('Escape')
        expect(page.locator('#sidebar-toggle')).to_have_attribute('aria-expanded', 'false')


def test_composer_picker_and_outside_dismissal(workspace):
    page, _ = workspace
    # Custom themes also show the larger companion at the composer.
    page.evaluate("""() => {
        document.documentElement.dataset.theme = 'forest';
        document.body.classList.remove('blank');
        document.querySelector('#hero').hidden = true;
    }""")
    page.locator('#perch .pet').click()
    menu = page.locator('#perch .companion-menu')
    expect(menu).to_be_visible()
    assert menu.evaluate("node => node.matches(':popover-open')")
    menu.get_by_label('Preview a reaction').select_option('browsing')
    page.locator('#bar').click()
    expect(menu).to_be_hidden()
    page.locator('#perch .pet').click()
    menu.get_by_role('button', name='Close companion picker').click()
    expect(menu).to_be_hidden()
    page.locator('#brand-pet .pet').click()
    expect(page.locator('#brand-pet .companion-menu')).to_be_visible()
    expect(menu).to_be_hidden()


def test_picker_closes_when_sidebar_collapses(workspace):
    page, _ = workspace
    page.locator('#brand-pet .pet').click()
    menu = page.locator('#brand-pet .companion-menu')
    expect(menu).to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(menu).to_be_hidden()
    expect(page.locator(':popover-open')).to_have_count(0)


@pytest.mark.parametrize('width', [1280, 390])
def test_older_webview_fallback_stays_above_workspace_and_keeps_focus(workspace, width):
    page, _ = workspace
    page.add_init_script('HTMLElement.prototype.showPopover = undefined; HTMLElement.prototype.hidePopover = undefined;')
    page.set_viewport_size({'width': width, 'height': 900})
    page.reload()
    if width <= 760:
        page.locator('#sidebar-toggle').click()
    button = page.locator('#brand-pet .pet')
    button.click()
    menu = page.get_by_role('dialog', name='Choose a companion')
    expect(menu).to_be_visible()
    assert menu.evaluate('node => node.parentElement === document.body')
    preview = menu.locator('.companion-preview')
    assert preview.evaluate("""node => {
        const r = node.getBoundingClientRect();
        return node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    }""")
    menu.get_by_role('searchbox').fill('fox')
    page.keyboard.press('Tab')
    expect(menu.locator('.row:visible')).to_be_focused()
    menu.get_by_label('Companion motion').select_option('still')
    page.keyboard.press('Escape')
    expect(menu).to_be_hidden()
    expect(button).to_be_focused()
    button.click()
    menu.get_by_role('button', name='Close companion picker').click()
    expect(menu).to_be_hidden()
