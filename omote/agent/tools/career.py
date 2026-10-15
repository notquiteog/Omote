"""Résumé exports and persistent job searches/applications, without fabricated submissions."""

import json

from omote.agent.tools.base import Assessment, Output, Tool, ToolError, resolve_in_root, truncate
from omote.career import MODELS, Records, Resume, database_path, paragraphs, render_docx, render_html, render_pdf
from omote.protocol.agent import Risk


class CareerTool(Tool):
    name = 'career'
    description = (
        'Build resumes and track job searches/applications in this project. save persists kind search '
        '(roles, city, work_mode, radius_km, employment, salary, preferences), resume (name, headline, '
        'contact[], summary, sections[{name,entries[{title,subtitle,dates,bullets[]}]}], sources[]), or job '
        '(title, company, city, actual url, search_id, status, salary, notes, documents[], confirmation). '
        'Use only user-provided/verified resume facts; never invent qualifications, employers or dates. '
        'list returns paginated summaries; get recovers complete saved work. Updates and delete require base_revision from get. Jobs deduplicate '
        'by source URL. Research current openings with web/browser tools using saved role/city filters; '
        'this tool does not search the internet or submit applications. Use browser_upload for reviewed '
        'documents, and browser_click intent=message for final application submission. Mark submitted '
        'only after observing confirmation and record that evidence; ready means prepared, not sent. '
        'export creates a single-column editable DOCX, PDF, HTML or TXT resume within allowed roots. '
        'PDF needs Chromium; other exports need no extra packages. Inspect the exported document before '
        'uploading. Records stay local to this user/project, separate from opt-in vector memory. '
        'Save tailored resumes as separate records so the original remains available.'
    )
    input_schema = {
        'type': 'object',
        'additionalProperties': False,
        'required': ['action'],
        'properties': {
            'action': {'type': 'string', 'enum': ['list', 'get', 'save', 'delete', 'export']},
            'kind': {'type': 'string', 'enum': ['search', 'resume', 'job']},
            'id': {'type': 'string'},
            'base_revision': {'type': 'integer', 'minimum': 1},
            'query': {'type': 'string', 'maxLength': 500},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30},
            'offset': {'type': 'integer', 'minimum': 0},
            'fields': {
                'type': 'object',
                'description': 'Complete search/resume/job record; see description for fields.',
            },
            'path': {
                'type': 'string',
                'description': 'Separate .docx, .pdf, .html or .txt output path in an existing directory.',
            },
            'overwrite': {'type': 'boolean', 'default': False},
        },
    }

    def assess(self, args, ctx):
        action = args.get('action')
        risk = Risk.READ if action in ('list', 'get') else Risk.WRITE
        if action == 'delete' or args.get('overwrite'):
            risk = Risk.DESTRUCTIVE
        try:
            if action not in ('list', 'get', 'save', 'delete', 'export'):
                raise ValueError('Choose list, get, save, delete or export.')
            if action in ('get', 'delete', 'export') and not isinstance(args.get('id'), str):
                raise ValueError('Use a saved career record ID.')
            if (
                args.get('id')
                and action in ('save', 'delete')
                and (type(args.get('base_revision')) is not int or args['base_revision'] < 1)
            ):
                raise ValueError('Get the record and supply its base_revision before updating/deleting.')
            if (
                type(args.get('limit', 10)) is not int
                or not 1 <= args.get('limit', 10) <= 30
                or type(args.get('offset', 0)) is not int
                or args.get('offset', 0) < 0
            ):
                raise ValueError('Use limit 1–30 and a nonnegative offset.')
            if not isinstance(args.get('query', ''), str) or len(args.get('query', '')) > 500:
                raise ValueError('Use a query up to 500 characters.')
            if 'kind' in args and args['kind'] not in MODELS:
                raise ValueError('Use search, resume or job.')
            if action == 'save':
                if args.get('kind') not in MODELS:
                    raise ValueError('Choose a record kind.')
                if len(json.dumps(args.get('fields', {}), allow_nan=False).encode()) > 256000:
                    raise ValueError('Career records are limited to 256 KB.')
                MODELS[args['kind']].model_validate(args.get('fields', {}))
            if action == 'export':
                path = resolve_in_root(args.get('path', ''), ctx)
                if (
                    path.suffix.lower() not in ('.pdf', '.docx', '.html', '.txt')
                    or not path.parent.is_dir()
                    or (path.exists() and not args.get('overwrite'))
                ):
                    raise ValueError('Use a separate .docx/.pdf/.html/.txt path; overwrites must be explicit.')
            return Assessment(risk=risk, summary=f'Career: {action} {args.get("kind", args.get("id", ""))}')
        except (ValueError, TypeError, OSError, ToolError) as error:
            return Assessment(risk=risk, summary='Career records', invalid=str(error))

    async def run(self, args, ctx):
        check = self.assess(args, ctx)
        if check.invalid:
            raise ToolError(check.invalid)
        records = Records(database_path(ctx.root))
        action = args['action']
        try:
            if action == 'list':
                data = records.list(
                    args.get('kind', ''), args.get('query', ''), args.get('offset', 0), args.get('limit', 10)
                )
            elif action == 'get':
                data = records.get(args['id'])
            elif action == 'save':
                data = records.save(args['kind'], args['fields'], args.get('id'), args.get('base_revision'))
            elif action == 'delete':
                data = records.delete(args['id'], args['base_revision'])
            else:
                saved = records.get(args['id'])
                if saved['kind'] != 'resume':
                    raise ValueError('Choose a resume record to export.')
                resume = Resume.model_validate(saved['fields'])
                path = resolve_in_root(args['path'], ctx)
                if path.suffix.lower() == '.pdf':
                    output = await render_pdf(resume)
                elif path.suffix.lower() == '.docx':
                    output = render_docx(resume)
                elif path.suffix.lower() == '.html':
                    output = render_html(resume)
                else:
                    output = '\n\n'.join(
                        ('• ' if kind == 'bullet' else '') + text for kind, text in paragraphs(resume)
                    ).encode()
                if ctx.checkpoint:
                    ctx.checkpoint.record(path)
                with path.open('wb' if args.get('overwrite') else 'xb') as stream:
                    stream.write(output)
                data = {
                    'id': saved['id'],
                    'revision': saved['revision'],
                    'path': str(path),
                    'bytes': len(output),
                    'note': 'Exported locally. Review facts/layout before sharing; no application was submitted.',
                }
        except (ValueError, TypeError, OSError) as error:
            raise ToolError(str(error)) from error
        content, clipped = truncate(json.dumps(data, ensure_ascii=False), 48000)
        return Output(content=content, truncated=clipped, display={'summary': f'Career: {action}'})
