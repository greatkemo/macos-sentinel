import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app
from dashboard.runtime import TelemetryHub
from dashboard.sessions import SessionStore
from test_dashboard import payload, request


class SessionTests(unittest.TestCase):
    def test_opt_in_persistence_privacy_retention_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory)
            self.assertFalse(store.enabled)
            hub=TelemetryHub()
            row=payload();row['processes']=[{'pid':123,'name':'SecretApp','cmdline':'private','cpu_percent':5,'memory_percent':2}]
            hub.publish(row)
            plain=store.save(hub.snapshot_session(),'Before')
            raw=store.read(plain['id'])
            self.assertFalse(raw['includes_apps']);self.assertNotIn('SecretApp',json.dumps(raw))
            full=store.save(hub.snapshot_session(),'After',True)
            raw=store.read(full['id'])
            self.assertIn('SecretApp',json.dumps(raw));self.assertNotIn('cmdline',json.dumps(raw));self.assertNotIn('pid',json.dumps(raw))
            store.configure(True,True)
            restored=SessionStore(directory)
            self.assertTrue(restored.enabled);self.assertTrue(restored.include_apps)
            self.assertEqual(len(restored.list()),2)
            restored.delete(plain['id']);self.assertEqual(len(store.list()),1)
            for i in range(12):store.save(hub.snapshot_session(),str(i))
            self.assertEqual(len(store.list()),10)
            with self.assertRaises(ValueError):store.read('../settings')

    def test_automatic_save_updates_same_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);hub=TelemetryHub();hub.publish(payload())
            first=store.save(hub.snapshot_session(),'Auto',automatic=True)
            hub.publish(payload(1));second=store.save(hub.snapshot_session(),'Auto',automatic=True)
            self.assertEqual(first['id'],second['id']);self.assertEqual(len(store.list()),1)
            self.assertEqual(store.list()[0]['count'],2)

    def test_app_snapshots_bounded_and_identifiers_excluded(self):
        hub=TelemetryHub(limit=3)
        for i in range(5):
            row=payload(i)
            row['processes']=[{'name':f'app{j}','cpu_percent':j,'memory_percent':20-j,'pid':j} for j in range(20)]
            hub.publish(row)
        self.assertEqual(len(hub.app_history),3)
        record=hub.app_history[-1]
        self.assertEqual(len(record['apps']),10)
        self.assertNotIn('pid',json.dumps(record))
        self.assertIn('app19',json.dumps(record));self.assertIn('app0',json.dumps(record))

    def test_adaptive_only_slows_after_continuous_idle_and_recovers(self):
        hub=TelemetryHub();hub.adaptive=True
        start=datetime.now(timezone.utc)
        def idle(seconds):
            row=payload(cpu=5);row['timestamp']=(start+timedelta(seconds=seconds)).isoformat()
            row['gpu']={'percent':5};row['memory']['pressure']={'level':'Normal'}
            return row
        self.assertEqual(hub.next_interval(idle(0)),1)
        for second in range(1,30):
            self.assertEqual(hub.next_interval(idle(second)),1)
        self.assertEqual(hub.next_interval(idle(30)),5)
        busy=idle(35);busy['cpu']['percent']=50
        self.assertEqual(hub.next_interval(busy),1)
        unknown=idle(70);unknown['gpu']['percent']=None
        self.assertEqual(hub.next_interval(unknown),1)
        hub.interval=10
        self.assertEqual(hub.next_interval(idle(80)),10)


class SessionAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_save_read_investigate_and_missing_gap(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'sessions',SessionStore(directory)),patch.object(app,'hub',TelemetryHub()):
            row=payload();row['processes']=[{'name':'Editor','cpu_percent':70,'memory_percent':2}];app.hub.publish(row)
            status,body,_=await request('/api/sessions','POST',{'name':'Test','include_apps':True})
            self.assertEqual(status,200);identity=json.loads(body)['id']
            status,body,_=await request('/api/sessions/'+identity)
            self.assertEqual(status,200);self.assertIn(b'Editor',body)
            at=datetime.fromisoformat(row['timestamp']).timestamp()
            status,body,_=await request('/api/investigate?at='+str(at))
            self.assertEqual(status,200);self.assertIn(b'Editor',body)
            status,_,_=await request('/api/investigate?at='+str(at-60))
            self.assertEqual(status,404)
            status,_,_=await request('/api/sessions/'+identity,'DELETE')
            self.assertEqual(status,200)
            status,_,_=await request('/api/sessions/'+identity)
            self.assertEqual(status,404)

    async def test_automatic_saving_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'sessions',SessionStore(directory)),patch.object(app,'hub',TelemetryHub()):
            app.hub.publish(payload())
            await app.save_automatic_session();self.assertEqual(app.sessions.list(),[])
            app.sessions.configure(True,False)
            await app.save_automatic_session();self.assertEqual(len(app.sessions.list()),1)
