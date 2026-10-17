"""Exercise the installed app's real Tauri update bridge in an isolated profile.

python desktop/ci/update_bridge.py /path/to/omote-desktop --static /path/to/omote/static
Linux needs a display (use xvfb-run). The fixture serves the real UI modules,
then invokes the native updater with a deliberately non-staged path. A browser
shim or a dead IPC bridge cannot return the native containment refusal.
With --handoff, Linux also runs the real bundled worker and replaces a tiny
temporary executable. This tests exit, replacement and relaunch, without
changing the installed package or exercising package-manager authentication.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('program', type=Path)
    parser.add_argument('--static', type=Path, required=True)
    parser.add_argument(
        '--handoff',
        action='store_true',
        help='Linux: exercise the real bundled worker with an isolated executable payload.',
    )
    args = parser.parse_args()
    if args.handoff and sys.platform != 'linux':
        parser.error('--handoff tests Linux AppImage replacement only')
    result = {}
    installer_path = '/not-a-staged-omote-update.deb'

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(args.static), **kw)

        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/healthz':
                body = json.dumps({'service': 'omote'}).encode()
            elif parsed.path == '/probe':
                result.update(parse_qs(parsed.query))
                body = b'ok'
            elif parsed.path == '/':
                body = (
                    args.static.joinpath('index.html').read_bytes()
                    + (
                        """<script type="module">
                import {isDesktop,installUpdate} from '/native.js';
                const answer=await installUpdate(INSTALLER_PATH);
                await fetch('/probe?desktop='+isDesktop+'&answer='+encodeURIComponent(answer));
                </script>""".replace('INSTALLER_PATH', json.dumps(installer_path))
                    ).encode()
                )
            else:
                # index.html imports /static/*, while this fixture's root is
                # the actual static directory from the source tree.
                if parsed.path.startswith('/static/'):
                    self.path = self.path.replace('/static/', '/', 1)
                return super().do_GET()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html' if parsed.path == '/' else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory(prefix='omote-update-bridge-') as temporary:
        root = Path(temporary)
        marker = root / 'launched-new-version'
        target = root / 'Installed Omote.AppImage'
        payload = b'#!/bin/sh\nprintf new-version > "$(dirname "$0")/launched-new-version"\n'
        if args.handoff:
            target.write_bytes(b'#!/bin/sh\nexit 1\n')
            target.chmod(0o755)
            staging = root / 'data' / 'updates'
            staging.mkdir(parents=True)
            installer = staging / 'Omote_fixture_amd64.AppImage'
            installer.write_bytes(payload)
            installer.chmod(0o755)
            installer.with_name(installer.name + '.verified.json').write_text(
                json.dumps(
                    {
                        'name': installer.name,
                        'version': '0.10.0-fixture',
                        'bytes': len(payload),
                        'sha256': hashlib.sha256(payload).hexdigest(),
                    }
                )
            )
            installer_path = str(installer)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env_file = root / '.env'
        env_file.write_text(
            f'OMOTE_PORT={server.server_port}\nOMOTE_DATA_DIR={root / "data"}\nOMOTE_UPDATE_CHECK=false\n'
        )
        env = dict(os.environ)
        for name in ('OMOTE_PORT', 'OMOTE_DATA_DIR', 'OMOTE_UPDATE_DIR'):
            env.pop(name, None)
        env.update(OMOTE_ENV=str(env_file), WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS='1')
        if sys.platform == 'linux':
            env.update(
                HOME=str(root),
                XDG_CACHE_HOME=str(root / 'cache'),
                XDG_CONFIG_HOME=str(root / 'config'),
                XDG_DATA_HOME=str(root / 'share'),
            )
        if args.handoff:
            env.update(APPIMAGE=str(target), APPDIR=str(root / 'appdir'))
        log = root.joinpath('app.log').open('wb')
        process = subprocess.Popen([str(args.program.resolve())], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while not (marker.exists() if args.handoff else result) and time.monotonic() < deadline:
                if process.poll() is not None and not args.handoff:
                    raise RuntimeError(
                        'Desktop exited before the bridge probe: '
                        + root.joinpath('app.log').read_text(errors='replace')
                    )
                time.sleep(0.1)
            if args.handoff:
                receipt = root / 'data' / 'updates' / 'install-result.json'
                details = receipt.read_text() if receipt.exists() else 'No installer receipt.'
                if (
                    not marker.exists()
                    or target.read_bytes() != payload
                    or not receipt.exists()
                    or json.loads(details).get('status') != 'complete'
                ):
                    logs = root / 'data' / 'updates' / 'install.log'
                    raise RuntimeError(
                        f'Native update handoff failed: {details}. '
                        + (logs.read_text(errors='replace') if logs.exists() else 'No worker log.')
                    )
                if process.poll() is None:
                    raise RuntimeError('The replacement launched before the original app exited.')
                print(
                    'Native Tauri command → bundled worker → old-app exit → verified file replacement → new executable launch passed.'
                )
                return
            if result.get('desktop') != ['true'] or "not in omote's update directory" not in str(result.get('answer')):
                raise RuntimeError(
                    f'Native updater IPC did not answer correctly: {result}. '
                    + root.joinpath('app.log').read_text(errors='replace')
                )
            print('Tauri 2 bridge reached the real native updater; outside-staging installer refused.')
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
            log.close()
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == '__main__':
    main()
