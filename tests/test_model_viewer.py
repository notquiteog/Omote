"""Render real meshes in Chromium and exercise resource isolation and disposal."""

import contextlib
import io
import json
import struct
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')

STATIC = Path(__file__).resolve().parents[1] / 'omote' / 'static'
ID = 'a' * 32
MODEL_PATH = f'/api/creative/assets/{ID}/file'


def glb(*, animated=False, textured=False):
    positions = struct.pack('<9f', -.5, -.5, 0, .5, -.5, 0, 0, .5, 0)
    doc = {
        'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(positions)}],
        'bufferViews': [{'buffer': 0, 'byteLength': len(positions)}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
                       'min': [-.5, -.5, 0], 'max': [.5, .5, 0]}],
        'materials': [{'doubleSided': True, 'pbrMetallicRoughness': {'baseColorFactor': [.8, .25, .08, 1]}}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'material': 0}]}],
        'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0,
    }
    if animated:
        doc['bufferViews'] += [{'buffer': 0, 'byteOffset': 36, 'byteLength': 8},
                               {'buffer': 0, 'byteOffset': 44, 'byteLength': 24}]
        doc['accessors'] += [{'bufferView': 1, 'componentType': 5126, 'count': 2, 'type': 'SCALAR', 'min': [0], 'max': [1]},
                             {'bufferView': 2, 'componentType': 5126, 'count': 2, 'type': 'VEC3'}]
        doc['animations'] = [{'name': 'Wave', 'samplers': [{'input': 1, 'output': 2, 'interpolation': 'LINEAR'}],
                              'channels': [{'sampler': 0, 'target': {'node': 0, 'path': 'translation'}}]}]
        positions += struct.pack('<8f', 0, 1, 0, 0, 0, .4, 0, 0)
        doc['buffers'][0]['byteLength'] = len(positions)
    if textured:
        image_module = pytest.importorskip('PIL.Image')
        output = io.BytesIO()
        image_module.new('RGB', (2, 2), (30, 180, 80)).save(output, format='PNG')
        image = output.getvalue()
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(image)})
        doc['images'] = [{'bufferView': len(doc['bufferViews']) - 1, 'mimeType': 'image/png'}]
        doc['textures'] = [{'source': 0}]
        doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture'] = {'index': 0}
        positions += image
        positions += b'\x00' * (-len(positions) % 4)
        doc['buffers'][0]['byteLength'] = len(positions)
    encoded = json.dumps(doc).encode()
    encoded += b' ' * (-len(encoded) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(encoded) + 8 + len(positions))
            + struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
            + struct.pack('<II', len(positions), 0x004E4942) + positions)


STL = b'Omote test'.ljust(80, b' ') + struct.pack('<I12fH', 1, 0, 0, 1, -.5, -.5, 0, .5, -.5, 0, 0, .5, 0, 0)


@pytest.fixture
def viewer():
    files = {MODEL_PATH: glb()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                content = (b'<!doctype html><html><body style="margin:0;background:#17191d;color:white">'
                           b'<div id="preview" style="width:640px;height:480px"></div><div id="toolbar"></div>'
                           b'</body></html>')
                mime = 'text/html'
            elif self.path in files:
                content, mime = files[self.path], 'application/octet-stream'
            elif self.path.startswith('/static/'):
                path = (STATIC / self.path.removeprefix('/static/')).resolve()
                if not path.is_relative_to(STATIC) or not path.is_file():
                    self.send_error(404)
                    return
                content, mime = path.read_bytes(), 'application/javascript'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as engine:
            try:
                browser = engine.chromium.launch(headless=True, args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            except playwright.Error as exc:
                pytest.skip(f'Chromium unavailable: {exc}')
            page = browser.new_page(viewport={'width': 900, 'height': 700})
            errors, requests = [], []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.on('request', lambda request: requests.append(request.url))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.evaluate('''() => {
                window.pendingFrames = new Set(); window.disposedContexts = 0;
                const request = window.requestAnimationFrame.bind(window), cancel = window.cancelAnimationFrame.bind(window);
                window.requestAnimationFrame = callback => {
                    const id = request(time => { pendingFrames.delete(id); callback(time); });
                    pendingFrames.add(id); return id;
                };
                window.cancelAnimationFrame = id => { pendingFrames.delete(id); cancel(id); };
                for (const prototype of [WebGLRenderingContext.prototype, WebGL2RenderingContext.prototype]) {
                    const original = prototype.getExtension;
                    prototype.getExtension = function(name) {
                        const extension = original.call(this, name);
                        if (!extension || name !== 'WEBGL_lose_context') return extension;
                        return new Proxy(extension, { get(target, property) {
                            if (property === 'loseContext') return () => { disposedContexts++; target.loseContext(); };
                            const value = target[property]; return typeof value === 'function' ? value.bind(target) : value;
                        }});
                    };
                }
            }''')
            yield page, files, requests
            page.evaluate('window.cleanModel?.()')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def mount(page, filename='triangle.glb', url=MODEL_PATH):
    page.evaluate('''async asset => {
        const { mountModel } = await import('/static/model-viewer.js');
        window.cleanModel = await mountModel(document.querySelector('#preview'), document.querySelector('#toolbar'), asset);
    }''', {'id': ID, 'url': url, 'filename': filename, 'name': 'Triangle', 'kind': 'model'})


@pytest.mark.parametrize('filename,data', [('triangle.glb', glb()), ('triangle.stl', STL)], ids=['GLB', 'STL'])
def test_real_mesh_renders_with_controls_and_resizes(viewer, filename, data):
    page, files, _ = viewer
    files[MODEL_PATH] = data
    mount(page, filename)
    assert page.locator('#preview').get_attribute('data-model-state') == 'ready', page.locator('#preview').inner_text()
    playwright.expect(page.locator('.model-stats')).to_contain_text('3 vertices · 1 triangles')
    page.get_by_role('button', name='Toggle ground grid').click()
    assert page.get_by_role('button', name='Toggle ground grid').get_attribute('aria-pressed') == 'false'
    page.get_by_role('button', name='Toggle wireframe').click()
    assert page.get_by_role('button', name='Toggle wireframe').get_attribute('aria-pressed') == 'true'
    page.get_by_role('button', name='Toggle wireframe').click()
    page.get_by_label('Model background').select_option('#edf0f3')
    page.get_by_role('button', name='Fit model in view').click()
    image_module = pytest.importorskip('PIL.Image')
    picture = image_module.open(io.BytesIO(page.locator('canvas').screenshot())).convert('RGB')
    colors = Counter(picture.get_flattened_data() if hasattr(picture, 'get_flattened_data') else picture.getdata())
    # Grid is hidden. Thousands of pixels differing from the background prove
    # that the geometry actually rendered, rather than merely parsing a file.
    background_count = colors.most_common(1)[0][1]
    assert picture.width * picture.height - background_count > 1000
    page.locator('#preview').evaluate('node => { node.style.width="400px"; node.style.height="300px"; }')
    page.wait_for_function('document.querySelector("canvas").width === 400')
    assert page.locator('canvas').get_attribute('height') == '300'
    page.locator('canvas').hover()
    page.mouse.wheel(0, 100)
    page.mouse.move(200, 180)
    page.mouse.down()
    page.mouse.move(260, 210, steps=5)
    page.mouse.up()
    assert page.locator('#preview').get_attribute('data-model-state') == 'ready'


def test_embedded_animation_plays_pauses_and_disposes_gpu_and_frames(viewer):
    page, files, _ = viewer
    files[MODEL_PATH] = glb(animated=True)
    mount(page)
    assert page.get_by_label('Animation clip').input_value() == '0'
    page.get_by_role('button', name='Play animation').click()
    page.wait_for_function('pendingFrames.size > 0')
    page.get_by_role('button', name='Pause animation').click()
    page.evaluate('window.cleanModel(); window.cleanModel();')
    assert page.locator('canvas').count() == 0
    assert page.locator('#toolbar').inner_text() == ''
    assert page.evaluate('pendingFrames.size') == 0
    assert page.evaluate('disposedContexts') == 1
    assert page.locator('#preview').get_attribute('data-model-state') == 'disposed'


@pytest.mark.parametrize('resource', ['https://attacker.invalid/texture.png', 'http://127.0.0.1:9999/secrets',
                                      '../private.bin', 'file:///etc/passwd',
                                      'data:image/svg+xml;base64,PHN2Zz4=', 'blob:https://attacker.invalid/a'])
def test_external_resource_rejected_before_loader_or_network(viewer, resource):
    page, files, requests = viewer
    files[MODEL_PATH] = json.dumps({'asset': {'version': '2.0'}, 'meshes': [], 'buffers': [{'uri': resource}]}).encode()
    mount(page, 'unsafe.gltf')
    playwright.expect(page.get_by_role('alert')).to_contain_text('External model resources are disabled')
    assert not any('attacker.invalid' in url or ':9999' in url or 'private.bin' in url for url in requests)
    assert page.locator('canvas').count() == 0


def test_model_url_must_belong_to_library_and_detach_cleans_up(viewer):
    page, _, requests = viewer
    mount(page, url='https://attacker.invalid/model.glb')
    playwright.expect(page.get_by_role('alert')).to_contain_text('Only files owned')
    assert not any('attacker.invalid' in url for url in requests)
    mount(page)
    page.locator('#preview').evaluate('node => node.remove()')
    page.wait_for_function('disposedContexts === 1')
    assert page.evaluate('pendingFrames.size') == 0


def test_new_mount_disposes_previous_preview_and_invalid_geometry_reports_error(viewer):
    page, files, _ = viewer
    mount(page)
    mount(page)
    assert page.evaluate('disposedContexts') == 1
    assert page.locator('canvas').count() == 1
    files[MODEL_PATH] = b'v 0 0 0\nv 0 0 0\nv 0 0 0\nf 1 2 3\n'
    mount(page, 'flat.obj')
    playwright.expect(page.get_by_role('alert')).to_contain_text('no finite, visible geometry')
    assert page.evaluate('disposedContexts') == 3
    assert page.evaluate('pendingFrames.size') == 0


def test_embedded_glb_texture_loads_without_external_requests(viewer):
    page, files, requests = viewer
    files[MODEL_PATH] = glb(textured=True)
    mount(page)
    assert page.locator('#preview').get_attribute('data-model-state') == 'ready'
    assert not any(url.startswith(('https:', 'file:')) for url in requests)
    assert page.locator('canvas').screenshot()
    page.evaluate('window.cleanModel()')
    assert page.evaluate('disposedContexts') == 1


def test_unavailable_webgl_reports_useful_error_without_leaking_frames(viewer):
    page, _, _ = viewer
    page.evaluate("""() => {
        const original = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
            if (kind.startsWith('webgl')) return null;
            return original.call(this, kind, ...args);
        };
    }""")
    mount(page)
    playwright.expect(page.get_by_role('alert')).to_contain_text('WebGL')
    assert page.evaluate('pendingFrames.size') == 0
    assert page.locator('canvas').count() == 0


def test_cancel_pending_model_does_not_clear_a_later_image_preview(viewer):
    page, _, _ = viewer
    page.evaluate("""async ({path, ident}) => {
        const { mountModel, unmountModel } = await import('/static/model-viewer.js');
        const original = window.fetch; let release;
        const started = new Promise(resolve => { window.modelFetchStarted = resolve; });
        window.fetch = async (url, options) => {
            if (String(url) === path) {
                window.modelFetchStarted();
                await new Promise(resolve => { release = resolve; });
            }
            return original(url, options);
        };
        const container = document.querySelector('#preview');
        const pending = mountModel(container, document.querySelector('#toolbar'), {
            id: ident, url: path, filename: 'old.glb', kind: 'model',
        });
        await started;
        unmountModel(container);
        const newer = document.createElement('p'); newer.id = 'new-asset'; newer.textContent = 'New image preview';
        container.replaceChildren(newer); release();
        window.cleanModel = await pending; window.fetch = original;
    }""", {'path': MODEL_PATH, 'ident': ID})
    assert page.locator('#new-asset').inner_text() == 'New image preview'
    assert page.locator('canvas').count() == 0
    assert page.evaluate('pendingFrames.size') == 0
    assert page.evaluate('disposedContexts') == 0
