from __future__ import annotations

from types import SimpleNamespace

import pytest

from omote.agent.approval import ApprovalPolicy, Decision, Mode
from omote.agent.tools.browser import BrowserClickTool, BrowserTypeTool, classify_click, classify_field
from omote.agent.tools.mail import MailTool
from omote.agent.tools.mcp import MCPToolAdapter
from omote.mcp.client import MCPTool
from omote.mcp.manager import ServerConfig
from omote.protocol.agent import Risk, ToolCall


@pytest.mark.parametrize('label', [
    '注文を確定する', '今すぐ予約', '立即购买', '确认支付', '立即預訂', '결제',
    'Jetzt kaufen', 'Zahlungspflichtig bestellen', 'Payer', 'Réserver maintenant',
    'Pagar', 'Reservar ahora', 'Acquista', 'Conferma prenotazione', 'Kup teraz',
    'Забронировать', 'تأكيد الحجز', 'ชำระเงิน', 'Ｐａｙ Ｎｏｗ',
])
def test_regional_commitment_labels_require_purchase_review(label):
    assert classify_click({'label': label})[0] is Risk.PURCHASE


@pytest.mark.parametrize('label', ['Send', 'Reply all', '送信', '发送', '發送', 'Envoyer', 'Senden', 'Enviar', 'Invia', 'Отправить', 'إرسال', '보내기'])
def test_sending_is_always_reviewed_in_every_mode(label):
    risk = classify_click({'label': label})[0]
    assert risk is Risk.MESSAGE
    for mode in Mode:
        call = ToolCall(id='c', name='browser_click', risk=risk, summary=label)
        assert ApprovalPolicy(mode=mode).decide(call)[0] is not Decision.ALLOW


@pytest.mark.parametrize('label', ['パスワード', '認証コード', '密码', '驗證碼', '비밀번호', 'mot de passe', 'contraseña', 'Kartennummer', 'пароль', 'كلمة المرور'])
def test_regional_secrets_do_not_appear_in_approval(label):
    field = {'label': label, 'type': 'text'}
    assert classify_field(field)[0] is Risk.CREDENTIAL
    tool = BrowserTypeTool(SimpleNamespace(last_elements={1: field}))
    assessment = tool.assess({'ref': 1, 'text': 'secret-value'}, None)
    assert assessment.risk is Risk.CREDENTIAL
    assert 'secret-value' not in assessment.summary


def test_declared_intent_only_raises_risk_and_enter_cannot_evade_it():
    browser = SimpleNamespace(last_elements={1: {'label': 'Continue'}, 2: {'label': 'Pay now'}})
    click = BrowserClickTool(browser)
    assert click.assess({'ref': 1, 'intent': 'purchase'}, None).risk is Risk.PURCHASE
    assert click.assess({'ref': 2, 'intent': 'message'}, None).risk is Risk.PURCHASE
    browser.last_elements[3] = {'tag': 'input', 'submit_label': '立即预订'}
    typing = BrowserTypeTool(browser)
    assert typing.assess({'ref': 3, 'text': 'Tokyo', 'submit': True}, None).risk is Risk.PURCHASE
    browser.last_elements[4] = {'tag': 'textarea', 'submit_label': ''}
    assert typing.assess({'ref': 4, 'text': 'Hello', 'submit': True}, None).risk is Risk.MESSAGE
    browser.last_elements[5] = {'type': 'password', 'submit_label': 'Pay now'}
    assert typing.assess({'ref': 5, 'text': 'secret', 'submit': True}, None).invalid


def test_mcp_cannot_declare_a_send_readonly():
    spec = MCPTool('send_email', 'Send an email', {}, {'readOnlyHint': True}, 'mail')
    tool = MCPToolAdapter(spec, ServerConfig('mail', trust_hints=True), None)
    assert tool.assess({}, None).risk is Risk.MESSAGE


def test_cc_and_bcc_are_visible_before_native_mail_send():
    tool = MailTool(None)
    assessment = tool.assess({'action': 'send', 'to': ['a@example.test'],
                              'cc': ['c@example.test'], 'bcc': ['b@example.test'],
                              'subject': 'Hello', 'body': 'Review me'}, None)
    assert all(address in assessment.summary for address in ['a@example.test', 'c@example.test', 'b@example.test'])


def test_workflow_skills_and_capabilities_are_discoverable(tmp_path):
    from omote.agent import prompt, skills
    found = skills.discover(tmp_path, home=tmp_path)
    assert {'global-assistant', 'business-admin', 'world-travel', 'email', 'photocraft'} <= set(found)
    text = prompt.build(tmp_path, capabilities=['mail', 'hr', 'trip', 'photocraft'])
    assert 'local leave ledger' in text
    assert 'does not book anything' in text
    assert 'photocraft' in text


async def test_real_browser_redacts_localized_labels_and_selects_options():
    from omote.agent.browser import INTERACTIVE_JS
    api = pytest.importorskip('playwright.async_api')
    async with api.async_playwright() as engine:
        try:
            browser = await engine.chromium.launch(headless=True)
        except api.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = await browser.new_page()
            await page.set_content("""
                <form><label for="pin">验证码</label><input id="pin" value="private-secret">
                <label for="country">Country</label><select id="country">
                  <option value="us">United States</option><option value="jp">日本</option>
                </select><button type="submit">立即预订</button></form>
            """)
            elements = await page.evaluate(INTERACTIVE_JS)
            secret = next(item for item in elements if item['id'] == 'pin')
            assert secret['secret'] and secret['value'] == ''
            assert 'private-secret' not in repr(elements)
            select = next(item for item in elements if item['id'] == 'country')
            assert select['label'] == 'Country'
            assert select['submit_label'] == '立即预订'
            assert select['options'][1] == {'value': 'jp', 'label': '日本'}

            class Live:
                last_elements = {e['ref']: e for e in elements}

                async def page(self):
                    return page

                async def find(self, ref):
                    return page.locator(f'[data-omote-ref="{ref}"]')

            tool = BrowserTypeTool(Live())
            assert tool.assess({'ref': select['ref'], 'text': '日本', 'submit': True}, None).risk is Risk.PURCHASE
            await tool.run({'ref': select['ref'], 'text': '日本'}, None)
            assert await page.locator('#country').input_value() == 'jp'
        finally:
            await browser.close()


def test_unread_references_cannot_bypass_commitment_or_secret_assessment():
    browser = SimpleNamespace(last_elements={})
    assert BrowserClickTool(browser).assess({'ref': 1, 'intent': 'purchase'}, None).invalid
    assert BrowserTypeTool(browser).assess({'ref': 1, 'text': 'secret'}, None).invalid


def test_mcp_message_recognition_does_not_lower_credential_risk():
    spec = MCPTool('send_password', 'Send a password', {}, {'readOnlyHint': True}, 'mail')
    tool = MCPToolAdapter(spec, ServerConfig('mail', trust_hints=True), None)
    assert tool.assess({}, None).risk is Risk.CREDENTIAL
