"""One bounded, cancellable directory scan. Only configured roots are traversable."""
import asyncio
import copy
import os
import signal
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException

class ScanManager:
    def __init__(self, roots=None):
        self.roots = [Path(p).resolve() for p in (roots or ['/Applications', Path.home()/'Library', Path.home()/'Downloads', Path.home()/'Documents'])]
        self.task = None
        self.state = None
        self.cache = {}
        self.baselines = {}

    def allowed(self, path):
        target = Path(path).resolve()
        if not any(target == root or target.is_relative_to(root) for root in self.roots):
            raise HTTPException(403, 'Directory is outside the configured scan roots')
        if not target.is_dir():
            raise HTTPException(404, 'Directory not found')
        return str(target)

    async def start(self, path=None, force=False):
        targets = [self.allowed(path)] if path else [str(p) for p in self.roots]
        if self.task and not self.task.done():
            if self.state['targets'] == targets:
                return self.snapshot()
            raise HTTPException(409, 'Cancel the active scan before scanning a different directory')
        key = tuple(targets)
        cached = self.cache.get(key)
        if not force and cached and time.monotonic()-cached[0] < 120:
            self.state = copy.deepcopy(cached[1])
            self.state['cached'] = True
            return self.snapshot()
        self.state = {'id':uuid.uuid4().hex, 'status':'running', 'targets':targets, 'directories':[], 'cached':False,
                      'scanned_at':datetime.now(timezone.utc).isoformat(), 'completed':0, 'total':len(targets)}
        self.task = asyncio.create_task(self.run(targets, key))
        return self.snapshot()

    def snapshot(self):
        result = copy.deepcopy(self.state or {'status':'idle','directories':[]})
        result['roots'] = [str(p) for p in self.roots]
        return result

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        return self.snapshot()

    async def close(self):
        await self.cancel()

    async def run(self, targets, key):
        try:
            for path in targets:
                row = {'path':path,'bytes':None,'children':[],'status':'running','error':None}
                self.state['directories'].append(row)
                await self.scan(path,row)
                self.state['completed'] += 1
            failures = [r for r in self.state['directories'] if r['status'] != 'success']
            self.state['status'] = 'partial' if failures else 'success'
            if len(failures) == len(targets) and not any(r['bytes'] is not None or r['children'] for r in failures):
                self.state['status'] = 'unavailable'
            self.state['finished_at'] = datetime.now(timezone.utc).isoformat()
            self.compare_completed()
            if len(self.cache) >= 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), self.snapshot())
        except asyncio.CancelledError:
            self.state['status'] = 'cancelled'
            for row in self.state['directories']:
                if row['status'] == 'running':
                    row.update(status='partial', error='Scan cancelled; sizes are incomplete')
            raise
        except Exception as exc:
            self.state.update(status='failure', error=str(exc)[:180])

    def compare_completed(self):
        """Compare only successful totals; partial scans never replace a baseline."""
        for row in self.state['directories']:
            if row['status'] != 'success' or row['bytes'] is None:
                row['comparison_note'] = 'Comparison unavailable for an incomplete scan'
                continue
            previous = self.baselines.get(row['path'])
            if previous:
                row['previous_scanned_at'] = previous['at']
                row['delta_bytes'] = row['bytes'] - previous['bytes']
                for child in row['children']:
                    if child['path'] in previous['children']:
                        child['delta_bytes'] = child['bytes'] - previous['children'][child['path']]
            else:
                row['comparison_note'] = 'First complete scan; refresh later to compare'
            # Bounded to 16 directories and the scanner's 200 children per directory.
            self.baselines.pop(row['path'], None)
            self.baselines[row['path']] = {'at': self.state['finished_at'], 'bytes': row['bytes'],
                'children': {c['path']: c['bytes'] for c in row['children']}}
            if len(self.baselines) > 16:
                self.baselines.pop(next(iter(self.baselines)))

    async def scan(self, path, row):
        proc = None
        stderr_task = None
        errors = bytearray()
        async def stderr():
            while block := await proc.stderr.read(4096):
                if len(errors) < 2048:
                    errors.extend(block[:2048-len(errors)])
        async def read():
            while line := await proc.stdout.readline():
                try:
                    size, entry = line.decode('utf-8', errors='replace').rstrip('\n').split('\t',1)
                    amount = int(size)*1024
                except ValueError:
                    continue
                if entry.rstrip('/') == path.rstrip('/'):
                    row['bytes'] = amount
                else:
                    row['children'].append({'path':entry,'bytes':amount,'depth':1})
                    row['children'].sort(key=lambda r:r['bytes'],reverse=True)
                    if len(row['children']) > 200:
                        row['children'].pop()
                        row['children_truncated'] = True
            await proc.wait()
            await stderr_task
        try:
            proc = await asyncio.create_subprocess_exec('/usr/bin/du','-x','-d','1','-k',path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=1024*1024)
            stderr_task = asyncio.create_task(stderr())
            await asyncio.wait_for(read(),90)
            row['error'] = errors.decode('utf-8',errors='replace').strip()[:300] or (f'du exited {proc.returncode}' if proc.returncode else None)
            row['status'] = 'partial' if row['error'] else 'success'
        except TimeoutError:
            row.update(status='partial',error='Timed out after 90 seconds; sizes are incomplete')
        except OSError as exc:
            row.update(status='unavailable',error=str(exc)[:180])
        finally:
            if proc and proc.returncode is None:
                try:
                    os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if stderr_task:
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                await proc.communicate()
            if stderr_task:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
