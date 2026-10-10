"""Discover the real ComfyUI frontend for the advanced Studio workspace.

The upstream frontend owns graph serialization, custom-node widgets, subgraphs,
and the Nodes 2.0 renderer. Embedding that frontend preserves these capabilities
across ComfyUI releases. API credentials stay on the server; the browser uses
the frontend's own authentication. No generic network proxy is exposed here.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter

from omote.providers.base import Modality, NoProviderError
from omote.providers.comfyui import ComfyUIProvider
from omote.providers.registry import registry

router = APIRouter(prefix='/api/media/comfyui')


def editor_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return ''
        _ = parsed.port  # Reject malformed ports before handing this to a browser.
        return value.rstrip('/') + '/'
    except ValueError:
        return ''


@router.get('/editors')
async def editors() -> dict:
    rows = []
    for info in registry.list():
        impl = None
        for modality in (Modality.IMAGE, Modality.VIDEO):
            try:
                candidate = registry.impl(info.id, modality)
            except NoProviderError:
                continue
            if isinstance(candidate, ComfyUIProvider):
                impl = candidate
                break
        if impl is None:
            continue
        conn = registry.connection(info.id)
        frontend = getattr(conn, 'frontend_url', '')
        url = editor_url(frontend or impl.base_url)
        reason = ''
        if registry.defaults.local_only and not info.local:
            reason = 'This connection is remote and local-only mode is enabled.'
        elif impl.transport.tor:
            reason = 'The embedded browser cannot use this connection’s Tor transport. Use Studio generation.'
        elif not url:
            reason = 'Set a browser-reachable HTTP(S) editor URL in Connections.'
        elif impl.api_key and not frontend:
            reason = 'Set an editor URL in Connections and sign in there. API keys are never sent to the frame.'
        rows.append({'id': info.id, 'label': info.label, 'url': '' if reason else url,
                     'available': not reason, 'reason': reason})
    return {'editors': rows}
