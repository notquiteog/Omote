"""Mail is a tool, and there is exactly one door onto the mailbox.

This file used to be about a drawer: a rail button, a panel with folders and a
reader, and the checks that it could not send anything on its own. The drawer
is gone. Those checks were still worth having, so they are here again pointed
at the thing that replaced it.

**Why it went.** The `mail` tool already existed, so the drawer was a *second*
door onto one IMAP connection — and two doors is two places for the store to be
wrong. Worse, the two could not see each other: the drawer read your inbox
without the model knowing you had, so "what came in today" was something you
*did* and then something you had to *ask* about, and the second conversation
could not build on the first. Same for search, which had `web_search` and a
results panel.

**What replaced it.** The tool, and a renderer that draws the tool's `display`
payload into its card. The client no longer fetches anything for mail or
search: the model went and got it, and the result is shown where it was asked
about.

The refusals below are the point of the file. Every one of them is something a
future change could quietly undo:

* Nothing in the client can send. The model drafts; a send is the tool, and the
  tool is graded `MESSAGE` and asked for. A drawer with a send button is a
  second, quieter way for a message to leave without anybody reading it.
* No second client. If `mail.js` or `search.js` ever calls the API again, the
  door is open.
* The tool is offered even with no mailbox, because a model that cannot see
  the tool cannot tell you it cannot read your mail.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'openmirror' / 'static'
INDEX = (STATIC / 'index.html').read_text()
APP = (STATIC / 'app.js').read_text()
MAIL = (STATIC / 'mail.js').read_text()
SEARCH = (STATIC / 'search.js').read_text()


# -- there is one door, and it is the tool ------------------------------------


def test_the_drawer_is_gone():
    """Not "is not a mode" — gone. There is no rail row, no panel, and no
    module for it, so there is nothing to drift back into."""
    assert 'drawer-rail' not in INDEX
    assert 'drawer-search' not in INDEX
    assert 'drawer-mail' not in INDEX
    assert 'data-drawer' not in INDEX
    assert not (STATIC / 'drawers.js').exists()
    for name in ('wireDrawers', 'onDrawerChange', 'closeDrawer', 'openDrawer'):
        assert name not in APP, f'{name} is still wired up'


def test_search_is_a_tool_and_not_a_place():
    assert 'data-drawer="search"' not in INDEX
    assert 'pane-search' not in INDEX
    assert 'searchCard' in APP, 'the results have to be drawn somewhere'
    assert '/api/search?q=' not in SEARCH, 'the client must not search; the tool does'


def test_mail_is_a_tool_and_not_a_place():
    assert 'data-drawer="mail"' not in INDEX
    assert 'pane-mail' not in INDEX
    assert 'mail-pane' not in INDEX
    assert 'mailCard' in APP
    assert 'wireMail' not in APP, 'there is nothing left to wire'


def test_the_client_does_not_talk_to_mail_at_all():
    """The load-bearing one.

    Two doors onto one mailbox is the thing this restructure exists to remove,
    and the way it comes back is somebody adding an `api('/api/mail/...')` to
    the renderer "just to get the folders". So the whole mail renderer is
    asserted to make no requests of its own.
    """
    for call in re.findall(r"(?:api|json|post|send|fetch)\(\s*['\"`]([^'\"`]+)", MAIL):
        assert not call.startswith('/api/mail'), f'the client reached the server itself: {call}'
    assert '/api/mail' not in MAIL


# -- it still cannot send ------------------------------------------------------


def test_nothing_in_the_client_can_send_a_message():
    """The model drafts. A send is the tool, and the tool is graded `MESSAGE`
    and asked for in every mode including the most permissive one."""
    assert 'mail-send' not in INDEX
    assert 'mail/send' not in MAIL
    assert '/send' not in MAIL
    for source, name in ((INDEX, 'index.html'), (MAIL, 'mail.js'), (SEARCH, 'search.js')):
        assert not re.search(r'<button[^>]*>\s*Send', source, re.I), name


def test_a_draft_says_it_has_not_been_sent():
    """The word "drafted" on its own reads as a thing that happened; the card
    says what has not."""
    assert 'It has not been sent' in MAIL


def test_no_module_declares_send_twice():
    """Kept from the drawer version, and still true: two send paths is two
    places for a message to leave without being read."""
    sends = 0
    for path in STATIC.glob('*.js'):
        text = path.read_text()
        sends += len(re.findall(r"/api/mail/send", text))
    assert sends == 0, f'{sends} client-side send paths'


# -- the tool is reachable even with no mailbox --------------------------------


def test_the_mail_tool_is_offered_with_no_account_configured():
    """The thing that made this restructure necessary, as an assertion.

    It used to be gated on `mail_enabled`, which is off until an address is
    configured. So on a fresh install the model had no `mail` tool at all and
    therefore could not tell the person it could not read their mail — from its
    side the capability did not exist. Offered, the refusal is a sentence
    naming the three settings, which is something a person can act on.
    """
    from openmirror.agent.runtime import mail_tools
    from openmirror.config import Config

    empty = Config(workspace='.', data_dir='/nonexistent-for-this-test', mail_accounts='')
    names = [tool.name for tool in mail_tools(empty)]
    assert 'mail' in names, f'the model cannot see that mail is unavailable: {names}'


def test_the_toolsets_list_mail_even_though_the_install_may_not():
    from openmirror.agent.runtime import TOOLSETS

    assert 'mail' in TOOLSETS['mail']


# -- what the card shows ------------------------------------------------------


def test_a_listing_does_not_say_anything_was_sent():
    """A read cannot send. Asserted because a renderer is exactly the sort of
    place a stray button appears."""
    listing = MAIL[MAIL.index('function listing('):MAIL.index('function one(')]
    assert 'send' not in listing.lower()
    assert 'button' not in listing


def test_expanding_a_row_only_shows_what_was_already_fetched():
    """Reading is a separate call on purpose.

    `mail read` is a real fetch and it is what marks a message seen, so clicking
    a row in a listing must not change what the mailbox thinks you have read.
    The card therefore shows the snippet it was handed and nothing more.
    """
    row = MAIL[MAIL.index('function row('):MAIL.index('function head(')]
    assert '/api/mail' not in row
    assert 'snippet' in row


def test_search_results_carry_the_hits_and_not_just_a_count():
    """A count and a backend name leave the model with something it has to
    paraphrase and the person with a line of text that has lost the links."""
    assert 'results' in MAIL
    display = (Path(__file__).resolve().parents[1] / 'openmirror' / 'agent' / 'tools' / 'web.py').read_text()
    assert "'results': [" in display, 'the search tool is not putting its hits in the payload'
    assert "'query': query" in display


def test_the_read_in_place_goes_through_the_guarded_fetch():
    """Unchanged from the drawer, and still the reason to keep a reader at all:
    the same fetch the agent uses, so the private-address guard applies to a
    person clicking a link and not only to a model following one."""
    assert '/api/search/fetch' in SEARCH
    assert 'Private and link-local addresses are refused' in SEARCH


def test_the_mail_refusal_names_the_settings_that_fix_it():
    """A refusal nobody can act on is a refusal that wastes a turn."""
    import asyncio
    import tempfile

    from openmirror.agent.tools.base import ToolContext
    from openmirror.agent.tools.mail import MailTool
    from openmirror.mail.accounts import AccountStore

    with tempfile.TemporaryDirectory() as scratch:
        tool = MailTool(AccountStore(Path(scratch) / 'mail.json'))
        ctx = ToolContext(root=Path(scratch), cwd=Path(scratch), emit=None, ask=None, session_id='s')

        asked = asyncio.run(tool.run({'action': 'accounts'}, ctx))
        assert 'No mail account is configured' in asked.content
        for key in ('OPENMIRROR_MAIL_ADDRESS', 'OPENMIRROR_MAIL_IMAP_HOST',
                    'OPENMIRROR_MAIL_SMTP_HOST', 'OPENMIRROR_MAIL_PASSWORD'):
            assert key in asked.content, f'the refusal does not mention {key}'


def test_an_action_with_no_account_fails_loudly_rather_than_answering():
    """A *question* answers — "what accounts do I have" has a real answer, which
    is none — and an *action* must fail, because a refusal that comes back as a
    result reads like something happened."""
    import asyncio
    import tempfile

    from openmirror.agent.tools.base import ToolContext, ToolError
    from openmirror.agent.tools.mail import MailTool
    from openmirror.mail.accounts import AccountStore

    with tempfile.TemporaryDirectory() as scratch:
        tool = MailTool(AccountStore(Path(scratch) / 'mail.json'))
        ctx = ToolContext(root=Path(scratch), cwd=Path(scratch), emit=None, ask=None, session_id='s')
        try:
            asyncio.run(tool.run({'action': 'send', 'to': 'a@b.com', 'subject': 'x',
                                  'body': 'y'}, ctx))
        except ToolError as exc:
            assert 'no mail account is configured' in str(exc)
        else:
            raise AssertionError('a send with no account came back as a result')
