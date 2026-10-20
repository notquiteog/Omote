"""Project-scoped career records and simple, editable résumé exports."""

from __future__ import annotations

import hashlib
import html
import io
import json
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_max_length=5000)

    @field_validator('*', mode='before')
    @classmethod
    def printable_text(cls, value):
        def check(item):
            if isinstance(item, str) and any(
                not (
                    char in '\t\n\r'
                    or 0x20 <= ord(char) <= 0xD7FF
                    or 0xE000 <= ord(char) <= 0xFFFD
                    or 0x10000 <= ord(char) <= 0x10FFFF
                )
                for char in item
            ):
                raise ValueError('Document text contains unsupported control characters.')
            if isinstance(item, (list, dict)):
                for child in item.values() if isinstance(item, dict) else item:
                    check(child)

        check(value)
        return value


class Entry(Model):
    title: str = Field(min_length=1, max_length=300)
    subtitle: str = Field(default='', max_length=500)
    dates: str = Field(default='', max_length=200)
    bullets: list[str] = Field(default_factory=list, max_length=30)


class Section(Model):
    name: str = Field(min_length=1, max_length=200)
    entries: list[Entry] = Field(min_length=1, max_length=40)


class Resume(Model):
    name: str = Field(min_length=1, max_length=200)
    headline: str = Field(default='', max_length=300)
    contact: list[str] = Field(default_factory=list, max_length=10)
    summary: str = ''
    sections: list[Section] = Field(default_factory=list, max_length=15)
    sources: list[str] = Field(default_factory=list, max_length=40)


class Search(Model):
    roles: list[str] = Field(min_length=1, max_length=20)
    city: str = Field(min_length=1, max_length=300)
    work_mode: str = Field(default='any', pattern='^(any|remote|hybrid|onsite)$')
    radius_km: int | None = Field(default=None, ge=0, le=1000)
    employment: str = ''
    salary: str = ''
    preferences: str = ''


class Job(Model):
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=3000)
    search_id: str = Field(default='', max_length=100)
    status: str = Field(
        default='found', pattern='^(found|preparing|ready|submitted|interview|offer|rejected|withdrawn)$'
    )
    salary: str = ''
    notes: str = ''
    confirmation: str = ''
    documents: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('url')
    @classmethod
    def actual_url(cls, value):
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('Use the actual http(s) job page without embedded credentials.')
        query = urlencode(
            [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith('utm_')]
        )
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ''))


MODELS = {'resume': Resume, 'search': Search, 'job': Job}


def database_path(root: Path):
    from omote.config import config

    key = hashlib.sha256(f'{config.default_user}\0{root.resolve()}'.encode()).hexdigest()
    return config.data_dir / 'career' / key / 'records.sqlite3'


class Records:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute(
            'CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL, url TEXT UNIQUE)'
        )
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def row(row):
        return {key: row[key] for key in ('id', 'kind', 'revision', 'updated')} | {'fields': json.loads(row['data'])}

    def get(self, ident):
        if self.path.exists():
            with self.connect() as db:
                row = db.execute('SELECT * FROM records WHERE id=?', (ident,)).fetchone()
            if row:
                return self.row(row)
        raise ValueError('No such career record in this project.')

    def list(self, kind='', query='', offset=0, limit=20):
        if not self.path.exists():
            return {'records': [], 'total': 0}
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM records WHERE (?="" OR kind=?) ORDER BY updated DESC', (kind, kind)
            ).fetchall()
        rows = [self.row(row) for row in rows if query.casefold() in row['data'].casefold()]
        selected, used = [], 0
        for row in rows[offset : offset + limit]:
            keys = {
                'resume': ('name', 'headline'),
                'search': ('roles', 'city', 'work_mode'),
                'job': ('title', 'company', 'city', 'url', 'search_id', 'status', 'salary'),
            }[row['kind']]
            row['fields'] = {key: row['fields'][key] for key in keys}
            size = len(json.dumps(row).encode())
            if selected and used + size > 40000:
                break
            selected.append(row)
            used += size
        following = offset + len(selected)
        return {'records': selected, 'total': len(rows), 'next_offset': following if following < len(rows) else None}

    def save(self, kind, fields, ident=None, base_revision=None):
        value = MODELS[kind].model_validate(fields).model_dump()
        if kind == 'job':
            if (
                value['status'] in ('submitted', 'interview', 'offer', 'rejected', 'withdrawn')
                and not value['confirmation'].strip()
            ):
                raise ValueError(
                    'Record the observed confirmation/status evidence before marking an external outcome. A local draft is not a submitted application.'
                )
            if value['search_id'] and self.get(value['search_id'])['kind'] != 'search':
                raise ValueError('search_id must identify a saved job search in this project.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if ident:
                old = db.execute('SELECT * FROM records WHERE id=?', (ident,)).fetchone()
                if not old or old['kind'] != kind or old['revision'] != base_revision:
                    raise ValueError(
                        'Career record changed or is missing. Get it and supply its current base_revision.'
                    )
                revision = old['revision'] + 1
            else:
                ident, revision = uuid.uuid4().hex, 1
            url = value['url'] if kind == 'job' else None
            duplicate = db.execute('SELECT id FROM records WHERE url=? AND id<>?', (url, ident)).fetchone()
            if duplicate:
                raise ValueError(f'This job is already tracked as {duplicate["id"]}. Update that record instead.')
            db.execute(
                'INSERT INTO records VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,updated=excluded.updated,data=excluded.data,url=excluded.url',
                (ident, kind, revision, time.time(), json.dumps(value, ensure_ascii=False), url),
            )
        return self.get(ident)

    def delete(self, ident, base_revision):
        with self.connect() as db:
            result = db.execute('DELETE FROM records WHERE id=? AND revision=?', (ident, base_revision))
            if not result.rowcount:
                raise ValueError('Career record changed or is missing. Get its current revision before deleting.')
        return {'deleted': ident}


def paragraphs(resume):
    rows = [('name', resume.name)]
    rows += [('headline', resume.headline)] if resume.headline else []
    rows += [('contact', ' · '.join(resume.contact))] if resume.contact else []
    rows += [('heading', 'Summary'), ('body', resume.summary)] if resume.summary else []
    for section in resume.sections:
        rows.append(('heading', section.name))
        for entry in section.entries:
            rows.append(('title', entry.title))
            if entry.subtitle or entry.dates:
                rows.append(('detail', ' | '.join(filter(None, (entry.subtitle, entry.dates)))))
            rows += [('bullet', text) for text in entry.bullets]
    return rows


def render_html(resume):
    tags = {'name': 'h1', 'heading': 'h2', 'title': 'h3'}
    content = ''.join(
        f'<{tags.get(kind, "p")} class="{kind}">{html.escape(text)}</{tags.get(kind, "p")}>'
        for kind, text in paragraphs(resume)
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(resume.name)} — Resume</title><style>'
        '@page{size:A4;margin:17mm}*{box-sizing:border-box}body{font:11pt/1.4 Arial,sans-serif;color:#17212b;max-width:176mm;margin:20px auto;padding:0 12px;overflow-wrap:anywhere}'
        'h1{font-size:25pt;margin:0 0 3pt}h2{font-size:12pt;margin:15pt 0 5pt;border-bottom:1px solid #a5b0ba;padding-bottom:3pt;break-after:avoid}'
        'h3{font-size:11pt;margin:9pt 0 2pt;break-after:avoid}p{margin:3pt 0;white-space:pre-line;orphans:3;widows:3}'
        '.contact,.detail{font-size:10pt;color:#465361}.bullet{padding-left:11pt;position:relative}.bullet:before{content:"•";position:absolute;left:0}'
        '@media print{body{max-width:none;margin:0;padding:0}a{color:inherit}}'
        '</style></head><body>' + content + '</body></html>'
    ).encode()


def render_docx(resume):
    """Small, single-column OOXML document; no macros, tables or remote resources."""
    rows = []
    sizes = {'name': 48, 'headline': 24, 'heading': 24, 'title': 22}
    for kind, text in paragraphs(resume):
        bold = '<w:b/>' if kind in ('name', 'heading', 'title') else ''
        props = '<w:keepNext/>' if kind in ('heading', 'title') else ''
        if kind == 'bullet':
            props += '<w:ind w:left="200" w:hanging="200"/>'
            text = '• ' + text
        runs = '<w:br/>'.join(f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in text.split('\n'))
        rows.append(
            f'<w:p><w:pPr>{props}<w:spacing w:before="80" w:after="40"/></w:pPr><w:r><w:rPr>{bold}<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="{sizes.get(kind, 21)}"/></w:rPr>{runs}</w:r></w:p>'
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + ''.join(rows)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="964" w:right="964" w:bottom="964" w:left="964"/></w:sectPr></w:body></w:document>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            '_rels/.rels',
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr('word/document.xml', document)
    return output.getvalue()


async def render_pdf(resume):
    try:
        from omote.setup.browser import activate
        activate()
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise ValueError(
            'PDF export needs Chromium. Choose Settings → Browser → Set up browser. HTML/DOCX/TXT exports remain available.'
        ) from error
    async with async_playwright() as engine:
        try:
            browser = await engine.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001
            raise ValueError(
                'PDF export needs Chromium. Choose Settings → Browser → Set up browser. HTML/DOCX/TXT remain available.'
            ) from error
        try:
            page = await browser.new_page(java_script_enabled=False)
            await page.route('**/*', lambda route: route.abort())
            await page.set_content(render_html(resume).decode(), wait_until='load', timeout=15000)
            return await page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            await browser.close()
