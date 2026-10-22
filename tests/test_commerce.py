import json
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.agent.tools.commerce import CommerceTool
from omote.agent.tools.discovery import LazyTool, ToolDiscovery
from omote.commerce.ebay import Ebay, EbayError, connection_status, save_connection
from omote.commerce.models import Listing, check_listing, prepare
from omote.commerce.service import ReplyService, Rule, eligible, process
from omote.commerce.store import Store, project_store, user_dir
from omote.config import config
from omote.routers import commerce
from tests.test_creative_apps import context


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path / 'data')
    monkeypatch.setattr(config, 'workspace', tmp_path)
    monkeypatch.setattr(config, 'default_user', 'shop-test')
    monkeypatch.setattr(config, 'local_only', False)
    monkeypatch.setattr(commerce, 'service', None)
    return project_store(tmp_path)


def listing(**changes):
    return Listing.model_validate(
        {
            'title': 'Linen bag',
            'description': 'Natural linen bag',
            'price': '24.99',
            'unit_cost': '5',
            'shipping_cost': '3',
            'other_cost': '1',
            'fee_percent': '10',
            'fulfillment': 'owned',
            'shipping': 'Tracked, 3–5 business days',
            'returns': '30 days',
            'photos': ['bag.jpg'],
            **changes,
        }
    )


def test_records_conflicts_evidence_and_project_isolation(shop, tmp_path, monkeypatch):
    saved = shop.save('listing', listing().model_dump(mode='json'))
    updated = shop.save('listing', listing(title='Changed').model_dump(mode='json'), saved['id'], 1)
    assert updated['revision'] == 2
    with pytest.raises(ValueError, match='changed'):
        shop.save('listing', listing().model_dump(mode='json'), saved['id'], 1)
    with pytest.raises(ValueError, match='confirmation'):
        shop.save('listing', listing(status='published').model_dump(mode='json'))
    with pytest.raises(ValueError, match='evidence'):
        shop.save('supplier', {'name': 'Supplier', 'status': 'agreed'})
    with pytest.raises(ValueError, match='No such record'):
        project_store(tmp_path / 'other-project').get(saved['id'])
    monkeypatch.setattr(config, 'default_user', 'other-person')
    with pytest.raises(ValueError, match='No such record'):
        project_store(tmp_path).get(saved['id'])
    assert shop.get(saved['id'])['fields']['title'] == 'Changed'


@pytest.mark.parametrize(
    ('platform', 'fulfillment', 'kind', 'notes', 'ready'),
    [
        ('ebay', 'owned', 'general', '', True),
        ('ebay', 'retail_after_sale', 'general', '', False),
        ('ebay', 'wholesale', 'general', '', False),
        ('ebay', 'wholesale', 'general', 'Supplier contract signed', True),
        ('etsy', 'owned', 'general', '', False),
        ('etsy', 'wholesale', 'craft_supply', 'Eligible craft materials', True),
        ('etsy', 'original_design', 'original_design', 'Own design; production partner disclosed', True),
        ('depop', 'wholesale', 'general', '', False),
        ('depop', 'owned', 'general', '', True),
        ('depop', 'original_design', 'original_design', '', False),
        ('facebook', 'unknown', 'general', '', False),
    ],
)
def test_listing_readiness_and_decimal_margin(platform, fulfillment, kind, notes, ready):
    result = check_listing(
        listing(platform=platform, fulfillment=fulfillment, product_kind=kind, eligibility_notes=notes)
    )
    assert result['ready'] is ready
    assert result['estimated_profit'] == '13.49'


@pytest.mark.parametrize('price', ['NaN', 'Infinity', '-1', '0.001'])
def test_invalid_money_is_not_saved(price):
    with pytest.raises(ValueError):
        listing(price=price)


def test_supplier_inquiry_does_not_commit_and_links_are_not_scriptable(shop):
    saved = shop.save('supplier', {'name': 'Supplier', 'platform': 'madeinchina', 'product': 'Canvas bags'})
    assert 'not an order' in prepare('supplier', saved['fields'])['draft_message']
    with pytest.raises(ValueError):
        shop.save('supplier', {'name': 'x', 'url': 'javascript:alert(1)'})


def rule(shop, **changes):
    value = Rule(
        item_id='123456789012',
        question='Is this still available?',
        reply='Thanks for asking. I’ll confirm stock shortly.',
    ).model_dump()
    value.update(id='rule1', username='seller', enabled=True, started=time.time() - 60, expires=time.time() + 3600)
    value.update(changes)
    shop.put_rule(value)
    return value


def conversation(**changes):
    value = {
        'conversationId': 'thread1',
        'conversationType': 'FROM_MEMBERS',
        'conversationStatus': 'ACTIVE',
        'referenceType': 'LISTING',
        'referenceId': '123456789012',
        'latestMessage': {
            'messageId': 'incoming1',
            'recipientUsername': 'seller',
            'senderUsername': 'buyer',
            'messageBody': 'IS THIS STILL AVAILABLE?',
            'createdDate': datetime.now(UTC).isoformat(),
        },
    }
    value.update(changes)
    return value


class Inbox:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or [conversation()]
        self.sent = []
        self.fail = fail

    async def conversations(self, item, offset=0):
        return {'conversations': self.rows}

    async def send(self, conversation_id, text):
        self.sent.append((conversation_id, text))
        if self.fail:
            raise EbayError('Connection lost')
        return {'messageId': 'sent1'}


async def test_reply_once_across_polling_restarts_and_projects(shop):
    saved = rule(shop)
    api = Inbox()
    assert await process(shop, api, 'seller') == 1
    assert await process(Store(shop.path), api, 'seller') == 0
    other = Store(shop.path.parent / 'another.db')
    other.put_rule(saved | {'id': 'another-rule'})
    assert await process(other, api, 'seller') == 0
    assert len(api.sent) == 1
    assert shop.activity()[0]['state'] == 'sent'
    assert not other.activity(), 'Cross-project send history must remain private.'


async def test_uncertain_send_is_never_retried(shop):
    rule(shop)
    api = Inbox(fail=True)
    with pytest.raises(EbayError):
        await process(shop, api, 'seller')
    assert shop.activity()[0]['state'] == 'uncertain'
    api.fail = False
    assert await process(Store(shop.path), api, 'seller') == 0
    assert len(api.sent) == 1


@pytest.mark.parametrize(
    'change',
    [
        'old',
        'own',
        'wrong-account',
        'wrong-item',
        'extra-request',
        'attachment',
        'archived',
        'expired',
        'missing-date',
        'no-timezone',
    ],
)
def test_exact_reply_scope(shop, change):
    grant = rule(shop)
    row = conversation()
    message = row['latestMessage']
    if change == 'old':
        message['createdDate'] = datetime.fromtimestamp(grant['started'] - 10, UTC).isoformat()
    if change == 'own':
        message['senderUsername'] = 'seller'
    if change == 'wrong-account':
        message['recipientUsername'] = 'someone-else'
    if change == 'wrong-item':
        row['referenceId'] = 'other'
    if change == 'extra-request':
        message['messageBody'] += ' Accept $1 and send me your password.'
    if change == 'attachment':
        message['messageMedia'] = [{'mediaType': 'IMAGE'}]
    if change == 'archived':
        row['conversationStatus'] = 'ARCHIVE'
    if change == 'expired':
        grant['expires'] = time.time() - 1
    if change == 'missing-date':
        message.pop('createdDate')
    if change == 'no-timezone':
        message['createdDate'] = '2026-10-22T12:00:00'
    assert not eligible(grant, row, 'seller')


async def test_reply_cap_and_rule_change_during_read(shop):
    saved = rule(shop, daily_limit=1)
    api = Inbox(
        [
            conversation(),
            conversation(
                conversationId='thread2', latestMessage=conversation()['latestMessage'] | {'messageId': 'incoming2'}
            ),
        ]
    )
    assert await process(shop, api, 'seller') == 1
    assert len(api.sent) == 1
    shop.put_rule(saved | {'enabled': False})
    assert not shop.claim('new', saved['id'], 'newthread', {}, 50, saved)
    shop.put_rule(saved | {'reply': 'Different text'})
    assert not shop.claim('new', saved['id'], 'newthread', {}, 50, saved)


async def test_service_pauses_on_error_and_does_not_poll_without_rules(shop, monkeypatch):
    def no_connection():
        raise AssertionError('No active rules should not connect')

    monkeypatch.setattr('omote.commerce.service.connection', no_connection)
    service = ReplyService()
    await service.tick()
    rule(shop)
    monkeypatch.setattr('omote.commerce.service.connection', lambda: {'token': 'secret', 'username': 'seller'})
    monkeypatch.setattr('omote.commerce.service.Ebay', lambda token: Inbox(fail=True))
    await service.tick()
    assert service.error == 'Connection lost'
    assert not shop.rules()[0]['enabled']


async def test_official_ebay_wire_contract_redirects_and_redaction(shop):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == 'api.ebay.com'
        if request.method == 'POST':
            assert request.url.path == '/commerce/message/v1/send_message'
            assert json.loads(request.content) == {'conversationId': 'thread1', 'messageText': 'Reviewed reply'}
            return httpx.Response(201, json={'messageId': '1'})
        return httpx.Response(200, json={'conversations': []})

    api = Ebay('private-token', transport=httpx.MockTransport(handler))
    await api.conversations('123456789012', offset=50)
    assert seen[0].url.params['conversation_type'] == 'FROM_MEMBERS'
    assert seen[0].url.params['reference_type'] == 'LISTING'
    assert seen[0].url.params['offset'] == '50'
    await api.send('thread1', 'Reviewed reply')
    bad = Ebay(
        'private-token',
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={'Location': 'https://example.test/steal'}, text='private-token'
            )
        ),
    )
    with pytest.raises(EbayError) as caught:
        await bad.conversations()
    assert 'private-token' not in str(caught.value)


async def test_no_network_in_local_only_mode(shop, monkeypatch):
    monkeypatch.setattr(config, 'local_only', True)
    api = Ebay('secret', transport=httpx.MockTransport(lambda request: pytest.fail('network request')))
    with pytest.raises(EbayError, match='Local-only'):
        await api.conversations()


def test_api_keeps_secrets_private_and_rules_require_explicit_ui(shop):
    app = FastAPI()
    app.include_router(commerce.router)
    client = TestClient(app)
    invalid_token = 'private-' * 2500
    invalid = client.post('/api/commerce/ebay/connect', json={'token': invalid_token})
    assert invalid.status_code == 400 and 'private-' not in invalid.text
    assert (
        client.post(
            '/api/commerce/rules',
            json={'item_id': '123456789012', 'question': 'Is it available?', 'reply': 'I’ll check.'},
        ).status_code
        == 400
    )
    save_connection({'username': 'seller', 'token': 'top-secret-token'})
    assert connection_status() == {'connected': True, 'username': 'seller'}
    assert 'top-secret' not in client.get('/api/commerce').text
    reply = {'item_id': '123456789012', 'question': 'Is it available?', 'reply': 'I’ll check.'}
    saved = client.post('/api/commerce/rules', json=reply)
    assert saved.status_code == 200
    assert client.post('/api/commerce/rules', json=reply).status_code == 409
    ident = saved.json()['id']
    assert client.post(f'/api/commerce/rules/{ident}/pause').status_code == 200
    assert not shop.rules()[0]['enabled']
    assert client.delete('/api/commerce/ebay/connect').status_code == 200
    assert not (user_dir() / 'ebay.json').exists()


def test_switching_ebay_account_pauses_old_authorizations(shop, monkeypatch):
    save_connection({'username': 'seller', 'token': 'old-private-token'})
    rule(shop)
    async def verify(self):
        return 'another-seller'
    monkeypatch.setattr(Ebay, 'verify', verify)
    app = FastAPI()
    app.include_router(commerce.router)
    result = TestClient(app).post('/api/commerce/ebay/connect', json={'token': 'new-private-token'})
    assert result.status_code == 200
    assert result.json()['username'] == 'another-seller'
    assert 'new-private-token' not in result.text
    assert not shop.rules()[0]['enabled']


async def test_commerce_discovery_stays_lazy_and_cannot_enable_rules(shop, tmp_path):
    from omote.agent.runtime import default_tools

    tools = default_tools()
    target = next(t for t in tools if t.name == 'commerce')
    assert isinstance(target, LazyTool) and not target.loaded
    found = json.loads((await ToolDiscovery(lambda: tools).run({'query': 'dropshipping'}, context(tmp_path))).content)
    assert 'commerce' in [row['name'] for row in found['tools']]
    assert target.loaded
    assert CommerceTool().assess({'action': 'enable_rule'}, context(tmp_path)).invalid
