"""User-enabled, exact replies. Model output never grants unattended authority."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
import unicodedata
from datetime import datetime

from pydantic import Field

from omote.commerce.ebay import Ebay, connection
from omote.commerce.models import Model
from omote.commerce.store import Store, user_dir


class Rule(Model):
    item_id: str = Field(pattern=r'^\d{9,20}$')
    question: str = Field(min_length=3, max_length=500)
    reply: str = Field(min_length=1, max_length=2000)
    daily_limit: int = Field(default=10, ge=1, le=50)
    days: int = Field(default=7, ge=1, le=30)


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split()).rstrip('?.!')


def eligible(rule, conversation, username):
    message = conversation.get('latestMessage') or {}
    try:
        created = datetime.fromisoformat(message['createdDate'].replace('Z', '+00:00'))
        if created.tzinfo is None:
            return False
        timestamp = created.timestamp()
    except (ValueError, KeyError, TypeError):
        return False
    return bool(rule.get('enabled') and rule['started'] <= timestamp <= time.time() < rule['expires']
                and conversation.get('conversationType') == 'FROM_MEMBERS'
                and conversation.get('conversationStatus') == 'ACTIVE'
                and conversation.get('referenceType') == 'LISTING'
                and conversation.get('referenceId') == rule['item_id']
                and conversation.get('conversationId') and message.get('messageId')
                and str(message.get('recipientUsername', '')).casefold() == username.casefold()
                and message.get('senderUsername') and message['senderUsername'].casefold() != username.casefold()
                and not message.get('messageMedia')
                and normalized(message.get('messageBody', '')) == normalized(rule['question']))


async def process(store, api, username):
    sent = 0
    rules = [r for r in store.rules() if r.get('enabled') and r['expires'] > time.time() and r['username'] == username]
    for item in {r['item_id'] for r in rules}:
        offset = 0
        for _ in range(10):
            data = await api.conversations(item, offset=offset)
            conversations = data.get('conversations', [])
            if not isinstance(conversations, list):
                raise ValueError('Unexpected eBay inbox response. Automatic replies paused.')
            for conversation in conversations:
                matches = [rule for rule in rules if eligible(rule, conversation, username)]
                # Conflicting rules need a person to resolve them.
                if len(matches) != 1:
                    continue
                rule = matches[0]
                message = conversation['latestMessage']
                key = hashlib.sha256(f"{username}\0{message['messageId']}".encode()).hexdigest()
                receipt = {'account': username, 'item_id': item, 'reply': rule['reply'], 'message_id': message['messageId']}
                if not store.claim(key, rule['id'], conversation['conversationId'], receipt, rule['daily_limit'], rule):
                    continue
                result = await api.send(conversation['conversationId'], rule['reply'])
                store.finish(key, 'sent', receipt | {'receipt': result.get('messageId', '')})
                sent += 1
            offset += len(conversations)
            if not conversations or not data.get('next'):
                break
        else:
            raise ValueError('More than 500 conversations matched. Review the inbox before enabling replies again.')
    return sent


class ReplyService:
    def __init__(self):
        self.task = None
        self.lock = asyncio.Lock()
        self.last_check = 0
        self.error = ''

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def tick(self):
        async with self.lock:
            stores = [Store(path) for path in user_dir().glob('*.db')]
            active = [store for store in stores if any(r.get('enabled') and r['expires'] > time.time() for r in store.rules())]
            if not active:
                return
            try:
                settings = connection()
                api = Ebay(settings['token'])
                for store in active:
                    await process(store, api, settings['username'])
                self.error = ''
            except Exception as exc:  # A single account failure must not kill the daemon.
                self.error = str(exc) if isinstance(exc, ValueError) else 'Inbox check failed. Review eBay before resuming.'
                for store in active:
                    for rule in store.rules():
                        if rule.get('enabled'):
                            store.put_rule(rule | {'enabled': False, 'error': self.error})
            self.last_check = time.time()

    async def loop(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.tick()
            except Exception:
                self.error = 'Could not read saved reply rules. Review Shop before enabling replies again.'
