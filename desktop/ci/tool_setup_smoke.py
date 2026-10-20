"""Exercise real in-app setup against a fresh daemon, including frozen builds.

No preinstalled Playwright, Python tools, model weights, or hand-edited connections.
The daemon downloads its own runtimes. No generation model is invoked by this check.
"""
import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

UI_CHECK = '''
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as driver:
    browser = driver.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width":1440,"height":1000}, color_scheme="dark")
    page.add_init_script("localStorage.setItem('omote.welcome.v1', 'done')")
    page.goto(sys.argv[1])
    page.locator("#start-studio").click()
    page.locator("#studio-setup").click()
    expect(page.locator("#ts-open")).to_be_visible(timeout=15000)
    page.screenshot(path=str(Path(sys.argv[2]) / "setup-ready.png"))
    page.locator("#ts-open").click()
    frame = page.frame_locator("#comfy-frame")
    expect(frame.locator("canvas").first).to_be_visible(timeout=90000)
    # Use the user's actual capture button, including the initial handshake.
    page.get_by_role("button", name="Workflow library", exact=True).click()
    page.locator("#wb-capture").click()
    expect(page.locator("#wb-status")).to_contain_text("Captured the complete editor graph", timeout=45000)
    page.locator("#wb-collapse").click()
    page.screenshot(path=str(Path(sys.argv[2]) / "studio-editor.png"))
    browser.close()
print("PASS: Open studio displays the native editor and captures a graph without manual setup.", flush=True)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', type=Path)
    parser.add_argument('--data', type=Path)
    args = parser.parse_args()
    root = (args.data or Path(tempfile.mkdtemp(prefix='omote-setup-smoke-'))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OMOTE_', 'PLAYWRIGHT_', 'PERCH_'))}
    env.update(OMOTE_ENV=os.devnull, OMOTE_DATA_DIR=str(root), OMOTE_UPDATE_CHECK='false',
               OMOTE_HOST='127.0.0.1', OMOTE_PORT=str(port))
    command = [str(args.daemon.resolve())] if args.daemon else [sys.executable, '-m', 'omote.main']
    base = f'http://127.0.0.1:{port}'
    def request(path, body=None, timeout=30):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    def wait_for(predicate, seconds, label):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Daemon exited: ' + str(process.returncode))
            try:
                result = predicate()
                if result:
                    return result
            except urllib.error.URLError:
                pass
            time.sleep(1)
        raise RuntimeError('Timed out: ' + label)
    def stop_daemon():
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    def check_connections():
        browser = request('/api/browser')
        assert browser['browser'] == 'chromium'
        assert next(c for c in browser['choices'] if c['id'] == 'chromium')['installed']
        editors = request('/api/media/comfyui/editors')['editors']
        own = next(e for e in editors if e['id'] == 'omote-managed-comfyui')
        assert own['available']
    with (root / 'daemon.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        try:
            wait_for(lambda: request('/healthz'), 60, 'daemon startup')
            for target in ('playwright', 'comfyui'):
                request(f'/api/setup/{target}/start', {})
                last = ''
                def finished(target=target):
                    nonlocal last
                    row = request('/api/setup')[target]
                    if row['note'] != last:
                        print(target + ': ' + row['note'], flush=True)
                        last = row['note']
                    if row['state'] == 'failed':
                        raise RuntimeError(row['error'] + '\n' + row.get('log', ''))
                    return row if row['state'] == 'ready' else None
                wait_for(finished, 1800, target + ' setup')
            check_connections()
            request('/api/setup/comfyui/stop', {})
            assert request('/api/setup')['comfyui']['state'] == 'stopped'
            request('/api/setup/comfyui/start', {})
            wait_for(lambda: request('/api/setup')['comfyui']['state'] == 'ready', 180, 'studio restart')
            # Reopen the entire app, not just the service. Windows termination
            # also exercises recovery of the previously owned child process.
            stop_daemon()
            process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
            wait_for(lambda: request('/healthz'), 60, 'daemon restart')
            wait_for(lambda: request('/api/setup')['comfyui']['state'] == 'ready', 180, 'automatic restore')
            check_connections()
            assert request('/api/setup')['playwright']['state'] == 'ready'
            setup_root = root / 'tools/setup'
            manifest = json.loads((setup_root / 'browser.json').read_text())
            venv = setup_root / f'browser-env-{manifest["version"]}-py{manifest["python"]}'
            python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            ui_env = dict(env, PLAYWRIGHT_BROWSERS_PATH=str(setup_root / 'browsers'))
            subprocess.run([str(python), '-c', UI_CHECK, base, str(root)], env=ui_env, check=True, timeout=150)
            print('PASS: browser page interaction, ComfyUI health/schema, automatic connection, stop/start and app restart.', flush=True)
        finally:
            with contextlib.suppress(Exception):
                if process.poll() is None:
                    request('/api/setup/comfyui/stop', {})
            stop_daemon()
    return root


if __name__ == '__main__':
    main()
