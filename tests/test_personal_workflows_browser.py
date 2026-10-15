"""Real browser job search/upload/application and flight checkout against local fixtures."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from omote.agent.approval import ApprovalPolicy, Decision, Mode
from omote.agent.browser import BrowserConfig, BrowserSession
from omote.agent.tools.base import ToolError
from omote.agent.tools.browser import (
    BrowserClickTool,
    BrowserNavigateTool,
    BrowserReadTool,
    BrowserTypeTool,
    BrowserUploadTool,
)
from omote.career import Resume, render_docx
from omote.protocol.agent import Risk, ToolCall
from tests.test_career import RESUME
from tests.test_creative_apps import context


@pytest.fixture
def personal_site():
    state = {'uploads': [], 'applications': 0, 'bookings': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body):
            payload = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            if path.path == '/jobs' and query.get('city') == ['Chicago'] and query.get('role') == ['Support']:
                body = (
                    '<h1>Support jobs in Chicago</h1><a href="/apply">Technical Support · Example Company · Chicago</a>'
                )
            elif path.path == '/apply':
                body = """<h1>Example Company · Technical Support · Chicago</h1>
                <label for="resume">Resume</label><input type="file" id="resume" accept=".docx,.pdf">
                <p id="upload-status"></p><button id="submit-application">Submit application</button>
                <p id="receipt"></p><script>
                document.querySelector('#resume').onchange=async(e)=>{
                  const file=e.target.files[0];await fetch('/upload',{method:'POST',body:file,headers:{'X-Filename':file.name}});
                  document.querySelector('#upload-status').textContent='Attached '+file.name;};
                document.querySelector('#submit-application').onclick=async()=>{
                  const response=await fetch('/submit',{method:'POST'});document.querySelector('#receipt').textContent=await response.text();};
                </script>"""
            elif path.path == '/flight':
                body = '<h1>ORD → SFO · 2026-11-10 · 09:00 -06:00 → 11:30 -08:00</h1><p>1 traveler · Economy · carry-on included · USD 225 total · nonrefundable</p><form action="/ticket" method="POST"><button>Confirm booking</button></form>'
            else:
                body = '<h1>Careers search</h1><form action="/jobs"><label for="role">Role</label><input id="role" name="role"><label for="city">City</label><input id="city" name="city"><button>Search jobs</button></form>'
            self.respond('<!doctype html><html><body>' + body + '</body></html>')

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.path == '/upload':
                state['uploads'].append(
                    {
                        'name': self.headers.get('X-Filename'),
                        'bytes': len(data),
                        'content_type': self.headers.get('Content-Type'),
                    }
                )
                self.respond('Uploaded')
            elif self.path == '/submit':
                state['applications'] += 1
                self.respond('Application received: APP-TEST-001')
            else:
                state['bookings'] += 1
                self.respond('Flight booking confirmed: FLIGHT-TEST-001')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


async def test_job_search_document_upload_and_application_confirmation(personal_site, tmp_path):
    pytest.importorskip('playwright.async_api')
    url, state = personal_site
    browser = BrowserSession(BrowserConfig(profile_dir=tmp_path / 'browser'))
    ctx = context(tmp_path)
    navigate, read, click, typing, upload = (
        BrowserNavigateTool(browser),
        BrowserReadTool(browser),
        BrowserClickTool(browser),
        BrowserTypeTool(browser),
        BrowserUploadTool(browser),
    )
    try:
        try:
            await navigate.run({'url': url}, ctx)
        except Exception as error:
            if 'Executable doesn' in str(error):
                pytest.skip('Chromium not installed')
            raise
        await read.run({}, ctx)
        for name, text in [('role', 'Support'), ('city', 'Chicago')]:
            ref = next(ref for ref, row in browser.last_elements.items() if row['name'] == name)
            await typing.run({'ref': ref, 'text': text}, ctx)
        ref = next(ref for ref, row in browser.last_elements.items() if row['label'] == 'Search jobs')
        await click.run({'ref': ref}, ctx)
        result = await read.run({}, ctx)
        assert 'Support jobs in Chicago' in result.content
        ref = next(ref for ref, row in browser.last_elements.items() if row['tag'] == 'a')
        await click.run({'ref': ref}, ctx)
        await read.run({}, ctx)
        path = tmp_path / 'reviewed-resume.docx'
        path.write_bytes(render_docx(Resume.model_validate(RESUME)))
        ref = next(ref for ref, row in browser.last_elements.items() if row['type'] == 'file')
        args = {'ref': ref, 'paths': [str(path)]}
        assessment = upload.assess(args, ctx)
        assert assessment.risk is Risk.MESSAGE and not assessment.invalid
        for mode in Mode:
            assert (
                ApprovalPolicy(mode=mode).decide(
                    ToolCall(id='upload', name=upload.name, risk=assessment.risk, summary=assessment.summary)
                )[0]
                is not Decision.ALLOW
            )
        assert not state['uploads'] and not state['applications']
        # Simulate explicit upload approval on our local fixture only.
        await upload.run(args, ctx)
        page = await browser.page()
        await page.wait_for_selector('#upload-status:text("Attached")')
        assert state['uploads'][0]['bytes'] == path.stat().st_size
        assert (
            state['uploads'][0]['content_type']
            == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        await read.run({}, ctx)
        ref = next(ref for ref, row in browser.last_elements.items() if row['id'] == 'submit-application')
        assert click.assess({'ref': ref}, ctx).risk is Risk.MESSAGE
        assert state['applications'] == 0
        # Simulate separate final-submit approval, then inspect the actual receipt.
        await click.run({'ref': ref, 'intent': 'message'}, ctx)
        assert 'APP-TEST-001' in (await read.run({}, ctx)).content
        assert state['applications'] == 1
    finally:
        await browser.close()


async def test_flight_final_price_requires_review_then_receipt_is_observable(personal_site, tmp_path):
    pytest.importorskip('playwright.async_api')
    url, state = personal_site
    browser = BrowserSession(BrowserConfig(profile_dir=tmp_path / 'browser'))
    ctx = context(tmp_path)
    try:
        try:
            await BrowserNavigateTool(browser).run({'url': url + '/flight'}, ctx)
        except Exception as error:
            if 'Executable doesn' in str(error):
                pytest.skip('Chromium not installed')
            raise
        read, click = BrowserReadTool(browser), BrowserClickTool(browser)
        before = await read.run({}, ctx)
        assert 'USD 225 total' in before.content and 'carry-on included' in before.content
        ref = next(ref for ref, row in browser.last_elements.items() if row['label'] == 'Confirm booking')
        assessment = click.assess({'ref': ref}, ctx)
        assert assessment.risk is Risk.PURCHASE and state['bookings'] == 0
        for mode in Mode:
            assert (
                ApprovalPolicy(mode=mode).decide(
                    ToolCall(id='flight', name=click.name, risk=assessment.risk, summary=assessment.summary)
                )[0]
                is not Decision.ALLOW
            )
        await click.run({'ref': ref, 'intent': 'purchase'}, ctx)
        assert 'FLIGHT-TEST-001' in (await read.run({}, ctx)).content
        assert state['bookings'] == 1
    finally:
        await browser.close()


def test_upload_rejects_unread_inputs_escapes_wrong_formats_and_oversized_files(tmp_path):
    browser = SimpleNamespace(last_elements={1: {'tag': 'input', 'type': 'file', 'accept': '.pdf'}})
    tool, ctx = BrowserUploadTool(browser), context(tmp_path)
    assert tool.assess({'ref': 2, 'paths': ['resume.pdf']}, ctx).invalid
    assert tool.assess({'ref': 1, 'paths': ['../private.pdf']}, ctx).invalid
    (tmp_path / 'resume.txt').write_text('Reviewed resume')
    assert tool.assess({'ref': 1, 'paths': ['resume.txt']}, ctx).invalid
    with (tmp_path / 'huge.pdf').open('wb') as stream:
        stream.truncate(20 * 1024**2 + 1)
    assert tool.assess({'ref': 1, 'paths': ['huge.pdf']}, ctx).invalid


async def test_upload_rechecks_a_changed_element(tmp_path):
    class Locator:
        async def get_attribute(self, key):
            return 'text'

    class Browser:
        last_elements = {1: {'tag': 'input', 'type': 'file'}}

        async def find(self, ref):
            return Locator()

    (tmp_path / 'resume.txt').write_text('Reviewed resume')
    with pytest.raises(ToolError, match='changed'):
        await BrowserUploadTool(Browser()).run({'ref': 1, 'paths': ['resume.txt']}, context(tmp_path))
