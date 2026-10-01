"""Single-flight background refresh with bounded workers and explicit freshness."""
import copy
import threading
import time
from datetime import datetime, timezone


class BackgroundReading:
    def __init__(self, collect, fallback, interval=15):
        self.collect = collect
        self.value = fallback
        self.interval = interval
        self.lock = threading.Lock()
        self.running = False
        self.closed = False
        self.finished = None
        self.checked_at = None
        self.value_at = None
        self.duration_ms = None
        self.error = None

    def read(self):
        with self.lock:
            now = time.monotonic()
            if not self.closed and not self.running and (self.finished is None or now-self.finished >= self.interval):
                self.running = True
                threading.Thread(target=self._refresh, name='slow-reading', daemon=True).start()
            result = copy.deepcopy(self.value)
            result['freshness'] = {'checked_at': self.checked_at,
                'age_seconds': round(now-self.value_at, 1) if self.value_at is not None else None,
                'refreshing': self.running, 'duration_ms': self.duration_ms,
                'error': self.error, 'interval_seconds': self.interval}
            return result

    def _refresh(self):
        started = time.monotonic()
        try:
            value = self.collect()
            error = None
        except Exception as exc:
            value = None
            error = type(exc).__name__
        with self.lock:
            # Failed refreshes retain the last reading and its original timestamp.
            if value is not None:
                self.value = value
                self.value_at = time.monotonic()
                self.checked_at = datetime.now(timezone.utc).isoformat()
            self.error = error
            self.duration_ms = round((time.monotonic()-started)*1000, 1)
            self.finished = time.monotonic()
            self.running = False

    def close(self):
        with self.lock:
            self.closed = True
