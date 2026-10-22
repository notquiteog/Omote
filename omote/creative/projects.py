"""Local, revisioned production plans. Planning never starts a paid render."""
from __future__ import annotations

import json
import time
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omote.creative.store import CreativeStore

Text = Annotated[str, Field(max_length=20000)]
Id = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')]
Vector = Annotated[list[Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]], Field(min_length=3, max_length=3)]

TEMPLATES = [
    {'id': 'cinema', 'name': 'Cinematic story', 'icon': '◫', 'description': 'Direct a scene, one shot at a time.',
     'hint': 'Shape the camera, lighting and pace. Generate a still first, then a video.',
     'shots': ['Establish the world', 'Follow the action', 'Hold the final moment'], 'ratio': '16:9'},
    {'id': 'remix', 'name': 'Remix a video', 'icon': '↻', 'description': 'Restyle, transfer motion or replace a subject.',
     'hint': 'Add a source video, then choose a model designed for your transformation or build a ComfyUI workflow with AI.',
     'shots': ['Transform the source'], 'ratio': '16:9'},
    {'id': 'scene', 'name': 'Stage in 3D', 'icon': '◇', 'description': 'Place objects, light the set, frame the shot.',
     'hint': 'Block a scene locally with editable objects. Capture a frame as a generation reference.',
     'shots': ['Frame the scene'], 'ratio': '16:9'},
    {'id': 'marketing', 'name': 'Product campaign', 'icon': '▱', 'description': 'Turn a product into a focused visual story.',
     'hint': 'Add product references, then plan a hook, benefit and call to action.',
     'shots': ['Stop the scroll', 'Show the benefit', 'Make the offer'], 'ratio': '9:16'},
    {'id': 'effects', 'name': 'Visual effects', 'icon': '✧', 'description': 'Build a striking reveal or transformation.',
     'hint': 'Effects become editable creative direction. Results depend on the selected model.',
     'shots': ['Create the effect'], 'ratio': '9:16'},
    {'id': 'faceless', 'name': 'Narrated short', 'icon': '≋', 'description': 'Plan a story with visuals and a voiceover.',
     'hint': 'Keep narration and captions beside each shot. Choose Make final video when your visuals are ready.',
     'shots': ['Open with a question', 'Explain the idea', 'Leave a takeaway'], 'ratio': '9:16'},
]

DIRECTION = {
    'genre': ['Auto', 'Drama', 'Documentary', 'Adventure', 'Noir', 'Comedy', 'Science fiction'],
    'era': ['Auto', '1960s', '1980s', '1990s', '2000s', 'Contemporary', 'Future'],
    'camera': ['Auto', 'Clean digital', '35mm film', '8mm film', 'Handheld camcorder'],
    'lens': ['Auto', '24mm wide', '35mm natural', '50mm portrait', '85mm close-up', 'Anamorphic', 'Macro'],
    'focus': ['Auto', 'Shallow depth of field', 'Moderate depth of field', 'Deep focus'],
    'movement': ['Auto', 'Locked camera', 'Slow push in', 'Pull back', 'Orbit subject', 'Track subject', 'Pan left', 'Crane up'],
    'lighting': ['Auto', 'Soft daylight', 'Golden hour', 'Window light', 'Neon practicals', 'Silhouette', 'Studio softbox'],
    'palette': ['Auto', 'Warm earth tones', 'Cool blue', 'Black and white', 'Pastel', 'High contrast'],
    'tempo': ['Auto', 'Single continuous shot', 'Calm', 'Dynamic'],
    'emotion': ['Auto', 'Joy', 'Wonder', 'Tension', 'Sadness', 'Determination'],
}
EFFECTS = {
    'none': '',
    'orbit': 'An uninterrupted camera orbit reveals the subject while the background moves with clear parallax.',
    'miniature': 'The scene transforms into a detailed miniature set with a tilt-shift look, preserving the subject.',
    'particles': 'The subject dissolves into luminous particles, then reforms into the same recognizable silhouette.',
    'zoom': 'A rapid camera pullback reveals the surrounding landscape, ending in a wide aerial composition.',
    'freeze': 'The action freezes while the camera arcs around the suspended moment, then motion resumes.',
}


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class StageObject(Record):
    id: Id = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = Field(default='Object', min_length=1, max_length=100)
    shape: Literal['box', 'sphere', 'cylinder', 'person'] = 'box'
    position: Vector = Field(default_factory=lambda: [0, .5, 0])
    scale: Annotated[list[Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)]], Field(min_length=3, max_length=3)] = Field(default_factory=lambda: [1, 1, 1])
    rotation: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    color: str = Field(default='#9daabd', pattern=r'^#[0-9a-fA-F]{6}$')


class Stage(Record):
    objects: list[StageObject] = Field(default_factory=list, max_length=60)
    camera: Vector = Field(default_factory=lambda: [6, 4, 7])
    target: Vector = Field(default_factory=lambda: [0, .8, 0])
    fov: float = Field(default=45, ge=15, le=100, allow_inf_nan=False)
    light: float = Field(default=3, ge=0, le=10, allow_inf_nan=False)
    background: str = Field(default='#242830', pattern=r'^#[0-9a-fA-F]{6}$')

    @model_validator(mode='after')
    def unique_objects(self):
        if len({o.id for o in self.objects}) != len(self.objects):
            raise ValueError('Scene object IDs must be unique.')
        if sum((a - b) ** 2 for a, b in zip(self.camera, self.target, strict=True)) < .01:
            raise ValueError('Move the camera away from its target.')
        return self


class Shot(Record):
    id: Id = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = Field(default='Untitled shot', min_length=1, max_length=200)
    prompt: Text = ''
    narration: Text = ''
    caption: str = Field(default='', max_length=2000)
    seconds: int = Field(default=5, ge=1, le=30)
    reference_asset_ids: list[Id] = Field(default_factory=list, max_length=12)


class Project(Record):
    name: str = Field(min_length=1, max_length=200)
    template: Literal['cinema', 'remix', 'scene', 'marketing', 'effects', 'faceless'] = 'cinema'
    brief: Text = ''
    aspect_ratio: Literal['16:9', '9:16', '1:1', '4:5'] = '16:9'
    character_id: str = Field(default='', max_length=100)
    reference_asset_ids: list[Id] = Field(default_factory=list, max_length=12)
    source_asset_id: str = Field(default='', max_length=100)
    remix: Literal['restyle', 'motion', 'replace'] = 'restyle'
    effect: Literal['none', 'orbit', 'miniature', 'particles', 'zoom', 'freeze'] = 'none'
    direction: dict[str, str] = Field(default_factory=dict, max_length=12)
    shots: list[Shot] = Field(min_length=1, max_length=32)
    stage: Stage = Field(default_factory=Stage)

    @model_validator(mode='after')
    def choices(self):
        if len({s.id for s in self.shots}) != len(self.shots):
            raise ValueError('Shot IDs must be unique.')
        for key, value in self.direction.items():
            if key not in DIRECTION or value not in DIRECTION[key]:
                raise ValueError(f'Unknown direction: {key} = {value}')
        return self


class Conflict(ValueError):
    pass


def new_project(template: str, name: str, brief: str = '') -> dict:
    spec = next((t for t in TEMPLATES if t['id'] == template), None)
    if not spec:
        raise ValueError('Choose a project type.')
    shots = [Shot(title=title, prompt=f'{title}. {brief}'.strip()).model_dump() for title in spec['shots']]
    stage = Stage(objects=[StageObject(name='Subject', shape='person', position=[0, 0, 0]),
                           StageObject(name='Backdrop', position=[0, 1.5, -3], scale=[7, 3, .2])])
    return Project(name=name, template=template, brief=brief, aspect_ratio=spec['ratio'], shots=shots,
                   stage=stage if template == 'scene' else Stage()).model_dump()


class Projects:
    def __init__(self, library: CreativeStore):
        self.library = library
        with library._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS studio_projects (
                    id TEXT PRIMARY KEY, user TEXT NOT NULL, revision INTEGER NOT NULL,
                    document TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS studio_projects_user ON studio_projects(user, updated DESC);
                CREATE TABLE IF NOT EXISTS studio_takes (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL, revision INTEGER NOT NULL, job_id TEXT NOT NULL,
                    snapshot TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_exports (
                    job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, snapshot TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL
                );
            ''')

    @staticmethod
    def unpack(row):
        return {**json.loads(row['document']), 'id': row['id'], 'revision': row['revision'],
                'created': row['created'], 'updated': row['updated']}

    def get(self, user, ident):
        with self.library._db() as db:
            row = db.execute('SELECT * FROM studio_projects WHERE user=? AND id=?', (user, ident)).fetchone()
        return self.unpack(row) if row else None

    def list(self, user):
        with self.library._db() as db:
            rows = db.execute('SELECT * FROM studio_projects WHERE user=? ORDER BY updated DESC LIMIT 100', (user,)).fetchall()
        return [{'id': r['id'], 'revision': r['revision'], 'updated': r['updated'],
                 **{k: json.loads(r['document'])[k] for k in ('name', 'template', 'brief')}} for r in rows]

    def save(self, user, fields, ident=None, base_revision=None):
        document = Project.model_validate(fields).model_dump()
        refs = document['reference_asset_ids'] + [i for s in document['shots'] for i in s['reference_asset_ids']]
        for ref in refs:
            asset = self.library.get_asset(user, ref)
            if not asset or asset['kind'] != 'image':
                raise ValueError('Every reference must be an image in your library.')
        if document['source_asset_id']:
            asset = self.library.get_asset(user, document['source_asset_id'])
            if not asset or asset['kind'] != 'video':
                raise ValueError('Choose a source video from your library.')
        if document['character_id'] and not self.library.get_character(user, document['character_id']):
            raise ValueError('Choose a character from your library.')
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024:
            raise ValueError('Keep a project below 1 MB.')
        now = time.time()
        with self.library._db() as db:
            if ident:
                cursor = db.execute('UPDATE studio_projects SET document=?, revision=revision+1, updated=? '
                                    'WHERE id=? AND user=? AND revision=?',
                                    (encoded, now, ident, user, base_revision))
                if not cursor.rowcount:
                    if not db.execute('SELECT 1 FROM studio_projects WHERE id=? AND user=?', (ident, user)).fetchone():
                        return None
                    raise Conflict('This project changed elsewhere. Reopen it before saving; your draft is still here.')
            else:
                ident = uuid.uuid4().hex
                db.execute('INSERT INTO studio_projects VALUES (?,?,1,?,?,?)', (ident, user, encoded, now, now))
        return self.get(user, ident)

    def delete(self, user, ident):
        with self.library._db() as db:
            return bool(db.execute('DELETE FROM studio_projects WHERE user=? AND id=?', (user, ident)).rowcount)

    def prepare_asset(self, user, ident, media_store):
        asset = self.library.get_asset(user, ident)
        path = self.library.asset_path(user, ident)
        if not asset or not path:
            raise ValueError('A reference file is missing. Restore or remove it before generating.')
        with self.library._db() as db:
            row = db.execute('SELECT media_id FROM prepared_media WHERE asset_id=?', (ident,)).fetchone()
        if row and media_store.path(row[0]) and media_store.path(row[0]).is_file():
            return row[0]
        media = media_store.add(path.read_bytes(), kind=asset['kind'], media_type=asset['media_type'],
                                prompt=asset['name'], source='upload', params={'creative_asset_id': ident})
        with self.library._db() as db:
            db.execute('INSERT OR REPLACE INTO prepared_media VALUES (?,?)', (ident, media.id))
        return media.id

    def prepare(self, user, ident, shot_id, media_store):
        project = self.get(user, ident)
        if not project:
            raise ValueError('No such project.')
        shot = next((s for s in project['shots'] if s['id'] == shot_id), None)
        if not shot:
            raise ValueError('No such shot.')
        character = None
        if project['character_id']:
            character = self.library.prepare(user, project['character_id'], media_store)
            if not character:
                raise ValueError('The project character was removed. Choose another before generating.')
        refs = list(dict.fromkeys(project['reference_asset_ids'] + shot['reference_asset_ids']))
        image_ids = list(dict.fromkeys((character or {}).get('reference_media_ids', []) +
                                     [self.prepare_asset(user, i, media_store) for i in refs]))
        source = self.prepare_asset(user, project['source_asset_id'], media_store) if project['source_asset_id'] else None
        direction = '; '.join(f'{k.capitalize()}: {v}' for k, v in project['direction'].items() if v != 'Auto')
        parts = [shot['prompt'] or project['brief']]
        if project['brief'] and project['brief'] not in parts[0]:
            parts.append('Project context: ' + project['brief'])
        if character and character['prompt']:
            parts.append('Character continuity: ' + character['prompt'])
        if direction:
            parts.append('Visual direction: ' + direction + '.')
        if project['template'] == 'effects' and EFFECTS[project['effect']]:
            parts.append(EFFECTS[project['effect']])
        if project['template'] == 'remix':
            parts.append({'restyle': 'Restyle the source video while preserving its action and composition.',
                          'motion': 'Transfer the motion of the source video to the reference subject.',
                          'replace': 'Replace the source subject with the reference subject; preserve camera motion and timing.'}[project['remix']])
        return {'project_id': ident, 'shot_id': shot_id, 'revision': project['revision'],
                'name': project['name'], 'title': shot['title'], 'template': project['template'],
                'prompt': '\n\n'.join(filter(None, parts)), 'negative_prompt': (character or {}).get('negative_prompt', ''),
                'reference_media_ids': image_ids, 'source_media_id': source,
                'aspect_ratio': project['aspect_ratio'], 'seconds': shot['seconds'],
                'narration': shot['narration'], 'caption': shot['caption']}

    def takes(self, user, ident):
        with self.library._db() as db:
            rows = db.execute('SELECT t.* FROM studio_takes t JOIN studio_projects p ON p.id=t.project_id '
                              'WHERE p.user=? AND p.id=? ORDER BY t.created DESC LIMIT 200', (user, ident)).fetchall()
        return [{'id': r['id'], 'shot_id': r['shot_id'], 'revision': r['revision'], 'job_id': r['job_id'],
                 'snapshot': json.loads(r['snapshot']), 'result': json.loads(r['result'])} for r in rows]

    def track(self, project, shot, snapshot, job):
        ident = uuid.uuid4().hex
        with self.library._db() as db:
            db.execute('INSERT INTO studio_takes VALUES (?,?,?,?,?,?,?,?)',
                       (ident, project['id'], shot, project['revision'], job.id,
                        json.dumps(snapshot), json.dumps(job.to_json()), time.time()))
        def done(_):
            with self.library._db() as db:
                db.execute('UPDATE studio_takes SET result=? WHERE id=?', (json.dumps(job.to_json()), ident))
        job.task.add_done_callback(done)
        return ident

    def track_export(self, project, snapshot, job):
        with self.library._db() as db:
            db.execute('INSERT INTO studio_exports VALUES (?,?,?,?,?,?)',
                       (job.id, project['id'], project['revision'], json.dumps(snapshot),
                        json.dumps(job.to_json()), time.time()))

    def finish_export(self, job):
        with self.library._db() as db:
            db.execute('UPDATE studio_exports SET result=? WHERE job_id=?', (json.dumps(job.to_json()), job.id))

    def exports(self, user, ident):
        with self.library._db() as db:
            rows = db.execute('SELECT e.* FROM studio_exports e JOIN studio_projects p ON p.id=e.project_id '
                              'WHERE p.user=? AND p.id=? ORDER BY e.created DESC LIMIT 100', (user, ident)).fetchall()
        return [{'job_id': r['job_id'], 'revision': r['revision'], 'created': r['created'],
                 'snapshot': json.loads(r['snapshot']), 'result': json.loads(r['result'])} for r in rows]


def bind_references(prepared, schema, params):
    """Bind only declared media controls; never quietly discard an identity or source."""
    out = dict(params)
    images = prepared['reference_media_ids']
    if images:
        targets = [p for p in schema if p.kind == 'image' and
                   not any(s in p.name.lower() for s in ('mask', 'last', 'end', 'tail'))]
        if not targets:
            raise ValueError('This model has no image reference input. Choose a model that supports references.')
        remaining = list(images)
        for target in targets:
            if not remaining:
                break
            count = target.max_images if target.max_images is not None else len(remaining)
            expected = ','.join(remaining[:count])
            remaining = remaining[count:]
            if out.get(target.name) and out[target.name] != expected:
                raise ValueError('The project references changed. Prepare this shot again before generating.')
            out[target.name] = expected
        if remaining:
            raise ValueError(f'This model accepts {len(images) - len(remaining)} reference image(s); '
                             f'your shot has {len(images)}. Reduce references or choose another model.')
    if prepared['template'] == 'remix':
        if not prepared['source_media_id']:
            raise ValueError('Add a source video to remix this shot.')
        target = next((p for p in schema if p.kind == 'video'), None)
        if not target:
            raise ValueError('Choose a model with video input, or use Ask AI to build a compatible ComfyUI workflow.')
        if out.get(target.name) and out[target.name] != prepared['source_media_id']:
            raise ValueError('The source video changed. Prepare this shot again before generating.')
        out[target.name] = prepared['source_media_id']
    return out
