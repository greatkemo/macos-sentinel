import threading
import time
import unittest
from dashboard.background import BackgroundReading


class BackgroundReadingTests(unittest.TestCase):
    def wait_done(self, reading):
        deadline = time.monotonic()+2
        while reading.running and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertFalse(reading.running)

    def test_slow_refresh_does_not_block_or_duplicate(self):
        entered, release = threading.Event(), threading.Event()
        calls=[]
        def collect():
            calls.append(1);entered.set();release.wait(2)
            return {'value':42}
        reading=BackgroundReading(collect, {'value':None})
        try:
            self.assertIsNone(reading.read()['value'])
            self.assertTrue(entered.wait(1))
            for _ in range(20):
                self.assertTrue(reading.read()['freshness']['refreshing'])
            self.assertEqual(len(calls),1)
            release.set();self.wait_done(reading)
            result=reading.read()
            self.assertEqual(result['value'],42)
            self.assertIsNotNone(result['freshness']['checked_at'])
            result['value']=0
            self.assertEqual(reading.read()['value'],42)
        finally:
            release.set();reading.close()

    def test_failed_refresh_retains_reading_age_and_close_stops_refresh(self):
        reading=BackgroundReading(lambda:{'value':42},{'value':None},interval=0)
        reading.read();self.wait_done(reading)
        original_at=reading.value_at
        def fail():raise RuntimeError('failure')
        reading.collect=fail
        reading.read();self.wait_done(reading);reading.close()
        result=reading.read()
        self.assertEqual(result['value'],42)
        self.assertEqual(reading.value_at,original_at)
        self.assertEqual(result['freshness']['error'],'RuntimeError')
        self.assertFalse(result['freshness']['refreshing'])
