"""Start one local dashboard instance, then open it in the default browser."""
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def is_dashboard(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            html = response.read(256000)
            return b'sentinel-token' in html and b'macOS Sentinel' in html
    except (OSError, urllib.error.URLError):
        return False


def launch(open_browser=True):
    port = int(os.environ.get('SENTINEL_PORT', '8000'))
    if not 1 <= port <= 65535:
        raise RuntimeError('Invalid dashboard port')
    url = f'http://127.0.0.1:{port}'
    folder = ROOT / '.sentinel-data'
    folder.mkdir(mode=0o700, exist_ok=True)
    with (folder / 'launcher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not is_dashboard(url):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', port)) == 0:
                    raise RuntimeError(f'Port {port} is occupied by another service. Dashboard was not started.')
            python = ROOT / '.venv/bin/python'
            if not python.exists():
                raise RuntimeError('The project Python environment is missing. Follow README setup instructions first.')
            child = subprocess.Popen([str(python), '-B', str(ROOT / 'app.py')], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if is_dashboard(url):
                    break
                if child.poll() is not None:
                    raise RuntimeError('Dashboard startup failed. Run .venv/bin/python app.py in Terminal for details.')
                time.sleep(.25)
            else:
                raise RuntimeError('Dashboard is still starting. Try opening it again in a few seconds.')
    if open_browser:
        subprocess.run(['/usr/bin/open', url], check=True)
    return url


if __name__ == '__main__':
    try:
        print(launch('--no-open' not in sys.argv))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
