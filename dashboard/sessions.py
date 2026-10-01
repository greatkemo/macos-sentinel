"""Opt-in, bounded local session snapshots. No paths, PIDs or command lines."""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 12 * 1024 * 1024


class SessionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.enabled = False
        self.include_apps = False
        self.auto_id = uuid.uuid4().hex
        try:
            config = json.loads((self.directory / 'settings.json').read_text())
            self.enabled = config.get('enabled') is True
            self.include_apps = config.get('include_apps') is True
        except (OSError, ValueError):
            pass

    def _write(self, path, payload):
        blob = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
        if len(blob) > MAX_BYTES:
            raise ValueError('Session exceeds the 12 MiB limit; save a shorter recording')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(blob)
        os.replace(temporary, path)

    def configure(self, enabled, include_apps):
        with self.lock:
            self._write(self.directory / 'settings.json', {'enabled': enabled, 'include_apps': include_apps})
            self.enabled, self.include_apps = enabled, include_apps

    def path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ValueError('Invalid session identifier')
        return self.directory / (identity + '.json')

    def save(self, payload, name, include_apps=False, automatic=False):
        with self.lock:
            identity = self.auto_id if automatic else uuid.uuid4().hex
            data = {'id': identity, 'name': name[:80], 'saved_at': datetime.now(timezone.utc).isoformat(),
                    'samples': payload['samples'][-3600:], 'events': payload['events'][-100:],
                    'app_samples': payload.get('app_samples', [])[-1800:] if include_apps else [],
                    'includes_apps': include_apps}
            self._write(self.path(identity), data)
            files = sorted(self.directory.glob('[a-f0-9]' * 32 + '.json'), key=lambda p:p.stat().st_mtime, reverse=True)
            for path in files[10:]:
                path.unlink()
            return self.metadata(data)

    @staticmethod
    def metadata(data):
        samples = data['samples']
        return {key: data[key] for key in ('id','name','saved_at','includes_apps')} | {
            'count': len(samples), 'start': samples[0]['timestamp'] if samples else None,
            'end': samples[-1]['timestamp'] if samples else None}

    def list(self):
        with self.lock:
            result=[]
            for path in self.directory.glob('[a-f0-9]' * 32 + '.json'):
                try:
                    result.append(self.metadata(self.read(path.stem)))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            return sorted(result, key=lambda row:row['saved_at'], reverse=True)

    def read(self, identity):
        with self.lock:
            path = self.path(identity)
            if path.stat().st_size > MAX_BYTES:
                raise ValueError('Session file exceeds size limit')
            return json.loads(path.read_text())

    def delete(self, identity):
        with self.lock:
            self.path(identity).unlink()
