from tests.test_workspace_browser import playwright
from tests.test_workspace_browser import workspace as workspace  # noqa: F401


def test_shop_drafts_review_persistence_and_browser_handoff(workspace, tmp_path):
    page, url = workspace
    expect = playwright.expect
    page.get_by_role('button', name='Shop', exact=True).click()
    page.get_by_role('button', name='New listing', exact=True).click()
    page.locator('#shop-field-title').fill('Canvas bag <safe>')
    page.locator('#shop-field-description').fill('Natural canvas bag')
    page.locator('#shop-field-price').fill('25')
    page.locator('#shop-field-fulfillment').select_option('owned')
    page.locator('#shop-field-shipping').fill('Tracked in 3–5 days')
    page.locator('#shop-field-returns').fill('30 day returns')
    page.locator('#shop-field-photos').fill('bag.jpg')
    page.get_by_role('button', name='Save draft', exact=True).click()
    expect(page.locator('#shop-list')).to_contain_text('Canvas bag <safe>')
    assert page.locator('#shop-list safe').count() == 0
    page.get_by_role('button', name='Review', exact=True).click()
    expect(page.locator('#shop-preview')).to_contain_text('Estimated profit: USD 25.00')
    page.screenshot(path=str(tmp_path / 'shop-desktop.png'))
    page.reload()
    page.get_by_role('button', name='Shop', exact=True).click()
    expect(page.locator('#shop-list')).to_contain_text('Canvas bag <safe>')
    page.get_by_role('button', name='Continue with Omote', exact=True).click()
    expect(page.locator('#shop-dialog')).not_to_be_visible()
    assert 'commerce tool' in page.locator('#input').input_value()
    assert not page.locator('#sessions > li[aria-current]').count()
    assert len(page.request.get(url + '/api/commerce').json()['records']) == 1


def test_mobile_shop_website_login_and_empty_reply_state(workspace):
    page, _ = workspace
    expect = playwright.expect
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#sidebar-toggle').click()
    page.get_by_role('button', name='Shop', exact=True).click()
    expect(page.locator('#shop-dialog')).to_be_visible()
    assert not page.locator('body').evaluate("el => el.classList.contains('navigation-open')")
    assert page.locator('#shop-dialog').evaluate('el => el.scrollWidth <= el.clientWidth')
    page.get_by_role('button', name='Replies', exact=True).click()
    expect(page.locator('#shop-connection')).to_contain_text('Connect eBay')
    expect(page.locator('#shop-rule-form')).to_be_hidden()
    page.locator('#shop-open-site').click()
    assert 'Facebook Marketplace' in page.locator('#input').input_value()
    assert 'browser_hand_over' in page.locator('#input').input_value()
    assert not page.locator('#sessions > li[aria-current]').count()


def test_shop_reply_form_never_enables_on_open_and_can_pause(workspace):
    page, _ = workspace
    expect = playwright.expect
    mutations = []
    data = {
        'root': '/project',
        'records': [],
        'rules': [],
        'activity': [],
        'ebay': {'connected': True, 'username': 'seller'},
        'last_check': 0,
    }
    page.route('**/api/commerce?*', lambda route: route.fulfill(json=data))

    def enable(route):
        mutations.append(route.request.post_data_json)
        data['rules'] = [dict(mutations[-1], id='rule1', enabled=True, expires=9999999999)]
        route.fulfill(json=data['rules'][0])

    page.route('**/api/commerce/rules?*', enable)
    page.get_by_role('button', name='Shop', exact=True).click()
    page.get_by_role('button', name='Replies', exact=True).click()
    expect(page.locator('#shop-rule-form')).to_be_visible()
    assert not mutations
    form = page.locator('#shop-rule-form')
    form.locator('[name=item_id]').fill('123456789012')
    form.locator('[name=question]').fill('Is this available?')
    form.locator('[name=reply]').fill('I’ll check the stock for you.')
    page.get_by_role('button', name='Enable this reply', exact=True).click()
    assert not mutations
    form.locator('[name=consent]').check()
    page.get_by_role('button', name='Enable this reply', exact=True).click()
    expect(page.locator('#shop-rules')).to_contain_text('On · Item')
    assert len(mutations) == 1 and 'consent' not in mutations[0]

    def pause(route):
        data['rules'][0]['enabled'] = False
        route.fulfill(json={'paused': 'rule1'})

    page.route('**/api/commerce/rules/rule1/pause?*', pause)
    page.get_by_role('button', name='Pause', exact=True).click()
    expect(page.locator('#shop-rules')).to_contain_text('Paused or expired')
