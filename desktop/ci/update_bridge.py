"""Exercise the installed app's real Tauri bridge without executing an installer.

python desktop/ci/update_bridge.py /path/to/omote-desktop --static /path/to/omote/static
Linux needs a display (use xvfb-run). The fixture serves the real UI modules,
then invokes the native updater with a deliberately non-staged path. A browser
shim or a dead IPC bridge cannot return the native containment refusal.
"""

import argparse
import json
import os
import subprocess
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
    args = parser.parse_args()
    result = {}

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
                    + b"""<script type="module">
                import {isDesktop,installUpdate} from '/native.js';
                const answer=await installUpdate('/not-a-staged-omote-update.deb');
                await fetch('/probe?desktop='+isDesktop+'&answer='+encodeURIComponent(answer));
                </script>"""
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
        log = root.joinpath('app.log').open('wb')
        process = subprocess.Popen([str(args.program.resolve())], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while not result and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        'Desktop exited before the bridge probe: '
                        + root.joinpath('app.log').read_text(errors='replace')
                    )
                time.sleep(0.1)
            if result.get('desktop') != ['true'] or "not in omote's update directory" not in str(result.get('answer')):
                raise RuntimeError(
                    f'Native updater IPC did not answer correctly: {result}. '
                    + root.joinpath('app.log').read_text(errors='replace')
                )
            print('Tauri 2 bridge reached the real native updater; outside-staging installer refused.')
        finally:
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
