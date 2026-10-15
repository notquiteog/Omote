"""Career work survives turns, exports real documents and never invents a submission."""

import io
import json
import zipfile

import pytest

from omote.agent.approval import Mode
from omote.agent.documents import DocumentError, read_document, read_docx
from omote.agent.runtime import build_session
from omote.agent.tools.base import ToolError
from omote.agent.tools.career import CareerTool
from omote.career import Records, Resume, database_path, render_docx, render_html
from omote.config import config
from omote.protocol.agent import ToolCall
from tests.test_agent import ScriptedProvider
from tests.test_creative_apps import context

RESUME = {
    'name': 'Alex Rivera',
    'headline': 'Customer Support Specialist',
    'contact': ['Chicago, IL', 'alex@example.test'],
    'summary': 'Customer support professional experienced in documenting and resolving technical issues.',
    'sources': ['User-provided experience, reviewed 2026-10-15'],
    'sections': [
        {
            'name': 'Experience',
            'entries': [
                {
                    'title': 'Support Associate',
                    'subtitle': 'Example Company',
                    'dates': '2023–2026',
                    'bullets': [
                        'Resolved customer questions across email and chat.',
                        'Maintained clear troubleshooting documentation.',
                    ],
                }
            ],
        }
    ],
}


@pytest.fixture
def career(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path / 'private')
    return CareerTool(), context(tmp_path)


async def call(tool, ctx, **args):
    return json.loads((await tool.run(args, ctx)).content)


async def test_searches_resumes_and_applications_survive_and_conflicts_do_not_overwrite(career, tmp_path, monkeypatch):
    tool, ctx = career
    assert (await call(tool, ctx, action='list'))['total'] == 0
    assert not (tmp_path / 'private').exists(), 'A read must not create a career database.'
    search = await call(
        tool,
        ctx,
        action='save',
        kind='search',
        fields={
            'roles': ['Customer Support', 'Technical Support'],
            'city': 'Chicago',
            'work_mode': 'hybrid',
            'radius_km': 30,
        },
    )
    saved = await call(tool, ctx, action='save', kind='resume', fields=RESUME)
    reloaded = Records(database_path(ctx.root)).get(saved['id'])
    assert reloaded['fields'] == saved['fields']
    updated = await call(
        tool,
        ctx,
        action='save',
        kind='resume',
        id=saved['id'],
        base_revision=1,
        fields={**RESUME, 'headline': 'Technical Support Specialist'},
    )
    assert updated['revision'] == 2
    with pytest.raises(ToolError, match='changed'):
        await call(tool, ctx, action='save', kind='resume', id=saved['id'], base_revision=1, fields=RESUME)
    assert (await call(tool, ctx, action='get', id=saved['id']))['fields']['headline'] == updated['fields']['headline']
    job = {
        'title': 'Technical Support',
        'company': 'Example',
        'city': 'Chicago',
        'url': 'https://example.test/jobs/123?utm_source=board',
        'search_id': search['id'],
    }
    tracked = await call(tool, ctx, action='save', kind='job', fields=job)
    with pytest.raises(ToolError, match='already tracked'):
        await call(tool, ctx, action='save', kind='job', fields={**job, 'url': 'https://example.test/jobs/123#apply'})
    with pytest.raises(ToolError, match='confirmation'):
        await call(
            tool,
            ctx,
            action='save',
            kind='job',
            id=tracked['id'],
            base_revision=1,
            fields={**job, 'status': 'submitted'},
        )
    ready = await call(
        tool, ctx, action='save', kind='job', id=tracked['id'], base_revision=1, fields={**job, 'status': 'ready'}
    )
    assert ready['fields']['status'] == 'ready'
    submitted = await call(
        tool,
        ctx,
        action='save',
        kind='job',
        id=tracked['id'],
        base_revision=2,
        fields={
            **job,
            'status': 'submitted',
            'confirmation': 'Application page displayed APP-TEST-001; observed 2026-10-15.',
        },
    )
    assert submitted['fields']['status'] == 'submitted' and submitted['revision'] == 3
    with pytest.raises(ToolError, match='changed'):
        await call(tool, ctx, action='delete', id=ready['id'], base_revision=1)
    assert (await call(tool, ctx, action='list', kind='job', query='Chicago'))['total'] == 1
    summaries = await call(tool, ctx, action='list', kind='resume')
    assert 'sections' not in summaries['records'][0]['fields']
    assert (await call(tool, ctx, action='get', id=saved['id']))['fields']['sections']
    other = tmp_path / 'another-project'
    other.mkdir()
    assert (await call(tool, context(other), action='list'))['total'] == 0
    monkeypatch.setattr(config, 'default_user', 'another-user')
    assert (await call(tool, ctx, action='list'))['total'] == 0


async def test_real_editable_docx_html_and_text_exports_and_confinement(career, tmp_path):
    tool, ctx = career
    saved = await call(tool, ctx, action='save', kind='resume', fields=RESUME)
    for extension in ('docx', 'html', 'txt'):
        out = await call(tool, ctx, action='export', id=saved['id'], path=f'resume.{extension}')
        assert out['bytes'] > 200
        with pytest.raises(ToolError, match='overwrites'):
            await call(tool, ctx, action='export', id=saved['id'], path=f'resume.{extension}')
    document = read_document(tmp_path / 'resume.docx')
    assert 'Alex Rivera' in document.text and 'troubleshooting documentation' in document.text
    assert 'User-provided experience' not in document.text, 'Private provenance is not part of a resume.'
    assert tool.assess({'action': 'export', 'id': saved['id'], 'path': '../escape.docx'}, ctx).invalid
    rendered = render_html(Resume.model_validate({**RESUME, 'name': '<script>alert(1)</script>'})).decode()
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert tool.assess({'action': 'save', 'kind': 'resume', 'fields': {**RESUME, 'name': 'bad\0text'}}, ctx).invalid


async def test_actual_pdf_has_selectable_resume_text(career, tmp_path):
    pytest.importorskip('playwright.async_api')
    pypdf = pytest.importorskip('pypdf')
    tool, ctx = career
    saved = await call(tool, ctx, action='save', kind='resume', fields=RESUME)
    try:
        await call(tool, ctx, action='export', id=saved['id'], path='resume.pdf')
    except ToolError as error:
        if 'installed Chromium' in str(error):
            pytest.skip(str(error))
        raise
    reader = pypdf.PdfReader(tmp_path / 'resume.pdf')
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) == 1 and 'Alex Rivera' in text and 'Support Associate' in text


def test_word_reader_rejects_entities_and_keeps_headers_and_multirun_text():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>日本</w:t></w:r><w:r><w:t>語 &amp; English</w:t></w:r></w:p></w:body></w:document>',
        )
        archive.writestr(
            'word/header1.xml',
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Contact in header</w:t></w:r></w:p></w:hdr>',
        )
    text = read_docx(output.getvalue()).text
    assert '日本語 & English' in text and 'Contact in header' in text
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '<!DOCTYPE word [<!ENTITY x "expanded">]><word/>')
    with pytest.raises(DocumentError, match='entity'):
        read_docx(output.getvalue())
    assert read_docx(render_docx(Resume.model_validate(RESUME))).kind == 'docx'


async def test_readonly_sessions_can_discover_but_not_save_a_resume(career, tmp_path):
    tool, _ = career
    agent = build_session(
        root=tmp_path,
        provider=ScriptedProvider([]),
        model='test',
        tools=[tool],
        mode=Mode.READ_ONLY,
        agents=False,
        skills=False,
    )
    result = await agent.invoke(
        ToolCall(id='save', name='career', arguments={'action': 'save', 'kind': 'resume', 'fields': RESUME}), 'turn'
    )
    assert result.is_error and not database_path(tmp_path).exists()
    await agent.close()
