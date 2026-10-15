import re

from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace


def test_personal_task_starters_preserve_drafts_and_fit_mobile(workspace):
    page, _ = workspace
    expect = playwright.expect
    # The disconnected setup dialog is expected; starters prepare text only.
    career = page.get_by_role('button', name='Find jobs & build a résumé')
    expect(career).to_be_visible()
    career.click()
    expect(page.locator('#input')).to_have_value(re.compile('.*job-search.*'))
    page.locator('#welcome-close').click()
    page.locator('#input').evaluate(
        "e=>{e.value='Keep my existing draft';e.dispatchEvent(new Event('input',{bubbles:true}));}"
    )
    page.get_by_role('button', name='Plan & book travel').click()
    expect(page.locator('#input')).to_have_value('Keep my existing draft')
    page.locator('#welcome-close').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('.workspace-starters').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
    assert page.locator('#transcript .turn').count() == 0
