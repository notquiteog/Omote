"""Official eBay Message API. No private endpoints, cookies or paid models."""
from __future__ import annotations

import json
import os
import tempfile

import httpx

from omote.commerce.store import user_dir


class EbayError(ValueError):
    pass


def connection():
    try:
        return json.loads((user_dir() / 'ebay.json').read_text())
    except FileNotFoundError:
        raise EbayError('Connect eBay in Shop → Replies first.') from None


def connection_status():
    try:
        data = connection()
        return {'connected': True, 'username': data['username']}
    except EbayError:
        return {'connected': False}


def save_connection(data):
    directory = user_dir()
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=directory, prefix='.ebay-')
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(data, output)
        os.replace(name, directory / 'ebay.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Ebay:
    def __init__(self, token=None, transport=None):
        self.token = token or connection()['token']
        self.transport = transport

    async def request(self, method, path, **kwargs):
        from omote.config import config
        if config.local_only:
            raise EbayError('Local-only mode is on. eBay needs a network connection.')
        # Callers cannot supply a host; redirects must never forward credentials.
        try:
            # transport-exempt: authenticated eBay account API, not a model provider; fixed HTTPS origin and local-only enforced above.
            async with httpx.AsyncClient(timeout=25, follow_redirects=False, transport=self.transport) as client:
                response = await client.request(method, 'https://api.ebay.com' + path,
                                                headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'}, **kwargs)
        except httpx.HTTPError:
            raise EbayError('eBay could not be reached. If sending, check the conversation before retrying.') from None
        if response.status_code >= 300:
            hint = {401: 'Reconnect eBay; the access token expired.', 403: 'This eBay app or account lacks the required API permissions.',
                    429: 'eBay rate limit reached. Automatic replies have paused.'}.get(response.status_code, 'Check the account and request on eBay.')
            # Error bodies can echo tokens, buyer content or internal details.
            raise EbayError(f'eBay returned {response.status_code}. {hint}')
        try:
            return response.json() if response.content else {}
        except ValueError:
            raise EbayError('eBay returned an unreadable response. Check the site before retrying a send.') from None

    async def verify(self):
        identity = await self.request('GET', '/commerce/identity/v1/user/')
        username = identity.get('username')
        if not isinstance(username, str) or not username:
            raise EbayError('eBay did not return an account username. Include commerce.identity.readonly permission.')
        await self.conversations(limit=1)
        return username

    async def conversations(self, item='', offset=0, limit=50):
        params = {'conversation_type': 'FROM_MEMBERS', 'limit': limit, 'offset': offset}
        if item:
            params.update(reference_id=item, reference_type='LISTING')
        return await self.request('GET', '/commerce/message/v1/conversation', params=params)

    async def send(self, conversation_id, text):
        if not conversation_id or not text.strip() or len(text) > 2000:
            raise EbayError('A conversation and a reply of 1–2000 characters are required.')
        return await self.request('POST', '/commerce/message/v1/send_message',
                                  json={'conversationId': conversation_id, 'messageText': text})
