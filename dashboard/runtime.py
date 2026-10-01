"""Bounded telemetry delivery, aggregate history and sustained threshold events."""
import asyncio
import copy
import math
from collections import deque
from datetime import datetime, timezone

class TelemetryHub:
    def __init__(self, limit=3600):
        self.queues = set()
        self.history = deque(maxlen=limit)
        self.app_history = deque(maxlen=min(limit, 1800))
        self.adaptive = False
        self.effective_interval = 1.0
        self.idle_since = None
        self.adaptive_last_moment = None
        self.events = deque(maxlen=100)
        self.since = {}
        self.triggered = set()
        self.interval = 1.0
        self.latest = None
        self.last_moment = None
        self.rules = {key: {'enabled': True, 'threshold': threshold, 'duration_seconds': 15}
                      for key, threshold in [('CPU', 85), ('GPU', 85), ('Memory usage', 80)]}

    def configure_alerts(self, rules):
        self.rules = copy.deepcopy(rules)
        self.since.clear()
        self.triggered.clear()

    def alert_settings(self):
        return copy.deepcopy(self.rules)

    def publish(self, payload):
        self.latest = payload
        self.history.append(self.aggregate(payload))
        self.app_history.append(self.app_snapshot(payload))
        moment = datetime.fromisoformat(payload['timestamp']).timestamp()
        if self.last_moment is not None and moment-self.last_moment > max(5,self.effective_interval*3):
            self.since.clear()
            self.triggered.clear()
        self.last_moment = moment
        while self.history and datetime.fromisoformat(self.history[0]['timestamp']).timestamp() < moment-3600:
            self.history.popleft()
        while self.app_history and datetime.fromisoformat(self.app_history[0]['timestamp']).timestamp() < moment-3600:
            self.app_history.popleft()
        values = {'CPU': payload['cpu']['percent'], 'GPU': payload.get('gpu', {}).get('percent'),
                  'Memory usage': payload['memory']['percent']}
        for key, value in values.items():
            rule = self.rules[key]
            threshold, duration = rule['threshold'], rule['duration_seconds']
            if rule['enabled'] and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > threshold:
                start = self.since.setdefault(key, moment)
                if moment-start >= duration and key not in self.triggered:
                    self.events.append({'timestamp':payload['timestamp'], 'metric':key, 'threshold':threshold, 'duration_seconds':duration, 'value':value})
                    self.triggered.add(key)
            else:
                self.since.pop(key, None)
                self.triggered.discard(key)
        for queue in tuple(self.queues):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    def next_interval(self, payload):
        moment = datetime.fromisoformat(payload['timestamp']).timestamp()
        if self.adaptive_last_moment is not None and moment-self.adaptive_last_moment > max(5, self.effective_interval*3):
            self.idle_since = None
        self.adaptive_last_moment = moment
        gpu = payload.get('gpu', {}).get('percent')
        idle = (payload['cpu']['percent'] < 15 and gpu is not None and gpu < 15
                and payload['memory'].get('pressure', {}).get('level') == 'Normal'
                and not self.since)
        if not self.adaptive or not idle:
            self.idle_since = None
            self.effective_interval = self.interval
        else:
            if self.idle_since is None:
                self.idle_since = moment
            self.effective_interval = max(self.interval, 5) if moment-self.idle_since >= 30 else self.interval
        return self.effective_interval

    @staticmethod
    def app_snapshot(payload):
        groups = {}
        for row in payload.get('processes', []):
            name = str(row.get('application') or row.get('name') or 'Unknown')[:200]
            group = groups.setdefault(name, {'name':name, 'cpu_percent':0, 'memory_percent':0, 'count':0})
            for key in ('cpu_percent', 'memory_percent'):
                value = row.get(key)
                if isinstance(value, (int, float)) and math.isfinite(value):
                    group[key] += value
            group['count'] += 1
        selected = {}
        for key in ('cpu_percent', 'memory_percent'):
            for row in sorted(groups.values(), key=lambda r:r[key], reverse=True)[:5]:
                selected[row['name']] = row
        return {'timestamp':payload['timestamp'], 'apps':list(selected.values()),
                'partial': bool(payload.get('processes_truncated')), 'interval':payload.get('sample_interval',1)}

    def snapshot_session(self):
        return {'samples':list(self.history), 'events':list(self.events), 'app_samples':list(self.app_history)}

    @staticmethod
    def aggregate(p):
        return {'timestamp':p['timestamp'], 'interval':p.get('sample_interval',1), 'cpu_percent':p['cpu']['percent'], 'memory_percent':p['memory']['percent'], 'gpu_percent':p.get('gpu',{}).get('percent'),
                'download_bps':p['network']['download_bps'], 'upload_bps':p['network']['upload_bps'],
                'disk_read_bps':p.get('disk_io',{}).get('read_bps'), 'disk_write_bps':p.get('disk_io',{}).get('write_bps'),
                'swap_used':p['memory'].get('swap',{}).get('used')}

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.queues.add(queue)
        if self.latest is not None:
            queue.put_nowait(self.latest)
        return queue

    def series(self, seconds):
        cutoff = datetime.now(timezone.utc).timestamp()-seconds
        return [p for p in self.history if datetime.fromisoformat(p['timestamp']).timestamp() >= cutoff]
