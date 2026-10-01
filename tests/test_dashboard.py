"""Run with python -m unittest discover -s tests -v. No real processes are signaled."""
import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import app
from dashboard import collectors
from dashboard.common import CommandFailed
from dashboard.runtime import TelemetryHub
from dashboard.security import LocalSession
from dashboard.storage import ScanManager

async def request(path, method='GET', body=None, token=app.SESSION_TOKEN, origin='http://127.0.0.1:8000', host='127.0.0.1:8000'):
    messages=[]
    headers=[(b'host',host.encode())]
    if origin is not None: headers.append((b'origin',origin.encode()))
    if token is not None: headers.append((b'x-sentinel-token',token.encode()))
    headers.append((b'content-type',b'application/json'))
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,'scheme':'http','path':path.split('?')[0], 'raw_path':path.split('?')[0].encode(),'query_string':path.partition('?')[2].encode(),'root_path':'','headers':headers,'client':('127.0.0.1',1234),'server':('127.0.0.1',8000)}
    async def receive(): return {'type':'http.request','body':json.dumps(body).encode() if body is not None else b'', 'more_body':False}
    async def send(message): messages.append(message)
    await app.app(scope,receive,send)
    return messages[0]['status'], b''.join(m.get('body',b'') for m in messages), dict(messages[0]['headers'])

def payload(seconds=0, cpu=90, memory=20):
    return {'timestamp':(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(),
            'cpu':{'percent':cpu}, 'memory':{'percent':memory}, 'network':{'download_bps':1,'upload_bps':2}}

class Parsers(unittest.TestCase):
    def test_vm_page_size(self):
        result=collectors.parse_vm_stat('Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages active: 2.\nPages occupied by compressor: 3.\nPages speculative: 1.')
        self.assertEqual(result['active'],32768)
        self.assertEqual(result['compressed'],49152)
        self.assertEqual(result['speculative'],16384)
    def test_updates_unknown_never_healthy(self):
        for text in ('','Unexpected response','Neue Software verfügbar'):
            result=collectors.parse_softwareupdate(text)
            self.assertFalse(result['ok'])
            self.assertIsNone(result['pending'])
    def test_updates_no_updates(self):
        self.assertEqual(collectors.parse_softwareupdate('No new software available.')['status'],'success')
    def test_update_labels(self):
        text='* Label: Example-1\n Title: Example, Version: 1, Size: 200K, Recommended: YES, Action: restart'
        result=collectors.parse_softwareupdate(text)
        self.assertTrue(result['pending'])
        self.assertEqual(result['updates'][0]['action'],'restart')
    def test_apfs_failure(self):
        with patch.object(collectors,'_plist_or_json',side_effect=CommandFailed('failed')), patch.object(collectors.subprocess,'check_output',side_effect=OSError('failed')):
            result=collectors.collect_apfs()
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'],'unavailable')
    def test_battery_not_charging(self):
        result=collectors.parse_pmset("Now drawing from 'Battery Power'\n -InternalBattery-0 50%; discharging; 2:00 remaining")
        self.assertFalse(result['charging'])
        self.assertEqual(result['power_state'],'Battery')
    def test_network_bits_to_mbps(self):
        result=collectors.parse_network_quality('{"dl_throughput":123000000,"ul_throughput":1000000,"responsiveness":500}')
        self.assertEqual(result['downlink_mbps'],123)
    def test_app_kind(self):
        self.assertEqual(collectors.app_kind('/System/Applications/Safari.app', 'apple'), 'system')
        self.assertEqual(collectors.app_kind('/Applications/Safari.app', 'apple'), 'system')
        self.assertEqual(collectors.app_kind('/usr/libexec/helpd', None), 'system')
        self.assertEqual(collectors.app_kind('/Applications/Example.app', 'identified_developer'), 'third-party')
        self.assertEqual(collectors.app_kind('/Applications/Store.app', 'mac_app_store'), 'third-party')
    def test_core_order_not_invented(self):
        self.assertEqual(collectors.classify_cores(4,2,2),['Core']*4)
    def test_memory_breakdown_includes_residual(self):
        vm=Mock(total=100000,used=50000,free=10000,available=30000,percent=70,active=0,wired=0,inactive=0)
        with patch.object(collectors.psutil,'virtual_memory',return_value=vm),patch.object(collectors.psutil,'swap_memory'),patch.object(collectors,'collect_pressure',return_value={'status':'unavailable'}),patch.object(collectors.subprocess,'check_output',return_value='(page size of 4096 bytes)\nPages active: 2.\nPages free: 1.'):
            result=collectors.collect_memory(lambda *args:None)
        self.assertEqual(sum(result[k] for k in ('active','wired','compressed','vm_free','inactive','speculative','other')),100000)

class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_protected_get_needs_token(self):
        status,_,_=await request('/api/history',token=None)
        self.assertEqual(status,403)
    async def test_wrong_host_denied_even_with_token(self):
        status,_,_=await request('/api/history',host='evil.example:8000')
        self.assertEqual(status,403)
    async def test_cross_origin_mutation_never_signals(self):
        with patch.object(app.psutil,'Process') as process:
            status,_,_=await request('/api/process/kill/1234','POST',{'create_time':123},origin='https://evil.example')
            self.assertEqual(status,403)
            process.assert_not_called()
    async def test_missing_origin_mutation_denied(self):
        status,_,_=await request('/api/settings','POST',{'interval':2},origin=None)
        self.assertEqual(status,403)
    async def test_index_bootstrap_and_security_headers(self):
        status,body,headers=await request('/',token=None,origin=None)
        self.assertEqual(status,200)
        self.assertIn(app.SESSION_TOKEN.encode(),body)
        self.assertIn(f'v{app.APP_VERSION}'.encode(),body)
        self.assertNotIn(b'__APP_VERSION__',body)
        self.assertNotIn(b'<script src="https://',body)
        self.assertIn(b"frame-ancestors 'none'",headers[b'content-security-policy'])
        self.assertEqual(headers[b'cache-control'],b'no-store')
    async def test_websocket_origin_and_token(self):
        for origin, token, expected in [('https://evil.example', 'secret',False),('http://127.0.0.1:8000','wrong',False),('http://127.0.0.1:8000','secret',True)]:
            inner=AsyncMock(); middleware=LocalSession(inner,'secret'); sent=AsyncMock()
            await middleware({'type':'websocket','path':'/ws','headers':[(b'host',b'127.0.0.1:8000'),(b'origin',origin.encode()),(b'sec-websocket-protocol',f'sentinel, {token}'.encode())]},AsyncMock(),sent)
            self.assertEqual(inner.called,expected)
    async def test_creation_time_mismatch_never_signals(self):
        with patch.object(app.psutil,'Process') as factory:
            proc=factory.return_value;proc.create_time.return_value=456
            status,_,_=await request('/api/process/kill/1234','POST',{'create_time':123})
            self.assertEqual(status,409);proc.terminate.assert_not_called()
    async def test_valid_identity_uses_safe_termination(self):
        with patch.object(app.psutil,'Process') as factory:
            proc=factory.return_value;proc.create_time.return_value=123;proc.name.return_value='fake';proc.is_running.return_value=True
            status,_,_=await request('/api/process/kill/1234','POST',{'create_time':123})
            self.assertEqual(status,200);proc.terminate.assert_called_once()
    async def test_identity_required(self):
        status,_,_=await request('/api/process/kill/1234','POST',{})
        self.assertEqual(status,422)
    async def test_settings_limits(self):
        for interval in (0,11):
            status,_,_=await request('/api/settings','POST',{'interval':interval})
            self.assertEqual(status,422)
    async def test_export_allowlist(self):
        with patch.object(app,'hub',TelemetryHub()):
            p=payload();p['host']={'hostname':'private-host'};p['processes']=[{'name':'secret'}];app.hub.publish(p)
            status,body,_=await request('/api/export')
            self.assertEqual(status,200)
            self.assertNotIn(b'private-host',body);self.assertNotIn(b'secret',body)
            self.assertTrue(json.loads(body)['redacted'])

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_subscriber_does_not_block(self):
        hub=TelemetryHub(limit=3);slow=hub.subscribe();fast=hub.subscribe()
        for i in range(20):
            hub.publish(payload(i));self.assertEqual((await fast.get())['network']['download_bps'],1)
        self.assertEqual(slow.qsize(),1);self.assertEqual(len(hub.history),3)
        self.assertEqual((await slow.get())['timestamp'],hub.latest['timestamp'])
    async def test_sustained_alert_and_recovery(self):
        hub=TelemetryHub()
        for i in range(16):hub.publish(payload(i))
        self.assertEqual(len(hub.events),1)
        hub.publish(payload(16));self.assertEqual(len(hub.events),1)
        hub.publish(payload(17,cpu=0))
        for i in range(18,34):hub.publish(payload(i))
        self.assertEqual(len(hub.events),2)
    async def test_sample_gap_does_not_count_as_sustained(self):
        hub=TelemetryHub();hub.publish(payload());hub.publish(payload(30))
        self.assertEqual(len(hub.events),0)
    async def test_singleflight_caller_cancellation(self):
        flight=app.SingleFlight();started=asyncio.Event();release=asyncio.Event();count=0
        async def work():
            nonlocal count
            count+=1;started.set();await release.wait();return 42
        first=asyncio.create_task(flight.do(work));await started.wait()
        second=asyncio.create_task(flight.do(work));await asyncio.sleep(0)
        first.cancel();await asyncio.gather(first,return_exceptions=True);release.set()
        self.assertEqual(await second,42);self.assertEqual(count,1)
    async def test_command_cancellation_reaps_process(self):
        proc=Mock(returncode=None);started=asyncio.Event();calls=0
        async def communicate():
            nonlocal calls
            calls+=1
            if calls==1:started.set();await asyncio.Event().wait()
            return b'',b''
        proc.communicate=communicate
        with patch.object(app.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)):
            task=asyncio.create_task(app.run_command(['fake'],30));await started.wait();task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        proc.kill.assert_called_once();self.assertEqual(calls,2)
    async def test_sampler_stays_on_one_worker(self):
        threads=[];sampled=asyncio.Event();loop=asyncio.get_running_loop()
        class FakeSampler:
            def __init__(self):threads.append(threading.get_ident())
            def prime(self):threads.append(threading.get_ident())
            def snapshot(self):threads.append(threading.get_ident());loop.call_soon_threadsafe(sampled.set);return payload()
        hub=TelemetryHub();hub.interval=.01
        with ThreadPoolExecutor(max_workers=1) as executor,patch.object(app,'TelemetrySampler',FakeSampler),patch.object(app,'hub',hub):
            task=asyncio.create_task(app.broadcast_loop(executor));await asyncio.wait_for(sampled.wait(),2)
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(len(set(threads)),1);self.assertNotEqual(threads[0],threading.get_ident())
    async def test_system_force_bypasses_cache(self):
        with patch.object(app,'_system_info_cache',{'expires':float('inf'),'payload':{'checked_at':'old'}}),patch.object(app,'collect_system_info',return_value={'checked_at':'new'}) as collect:
            self.assertEqual((await app.get_system_info())['checked_at'],'old')
            self.assertEqual((await app.get_system_info(True))['checked_at'],'new');collect.assert_called_once()

class AlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_configurable_gpu_missing_samples_and_rearm(self):
        hub=TelemetryHub()
        rules=hub.alert_settings()
        rules['GPU'].update(threshold=50,duration_seconds=5)
        rules['CPU']['enabled']=False
        hub.configure_alerts(rules)
        def sample(i,gpu):
            value=payload(i,cpu=99);value['gpu']={'percent':gpu};return value
        for i in range(6):hub.publish(sample(i,70))
        self.assertEqual([event['metric'] for event in hub.events],['GPU'])
        hub.publish(sample(6,70));self.assertEqual(len(hub.events),1)
        hub.publish(sample(7,None))
        for i in range(8,14):hub.publish(sample(i,70))
        self.assertEqual(len(hub.events),2)
        hub.configure_alerts(rules)
        hub.publish(sample(14,70));self.assertEqual(len(hub.events),2)
        rules['GPU']['threshold']=1
        self.assertEqual(hub.rules['GPU']['threshold'],50)

    async def test_alert_validation_and_session_api(self):
        rules={key:{'enabled':True,'threshold':90,'duration_seconds':20} for key in ['cpu','gpu','memory']}
        with patch.object(app,'hub',TelemetryHub()):
            status,_,_=await request('/api/alerts','POST',rules)
            self.assertEqual(status,200)
            self.assertEqual(app.hub.rules['GPU']['threshold'],90)
            rules['gpu']['duration_seconds']=0
            status,_,_=await request('/api/alerts','POST',rules)
            self.assertEqual(status,422)
            self.assertEqual(app.hub.rules['GPU']['duration_seconds'],20)

class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_path_escape_and_symlink_denied(self):
        with tempfile.TemporaryDirectory() as root,tempfile.TemporaryDirectory() as outside:
            scans=ScanManager([root]);Path(root,'link').symlink_to(outside)
            for path in (outside,Path(root,'link')):
                with self.assertRaises(app.HTTPException):scans.allowed(str(path))
    async def test_progress_cancel_and_force(self):
        with tempfile.TemporaryDirectory() as root:
            scans=ScanManager([root]);entered=asyncio.Event();release=asyncio.Event()
            async def scan(path,row):entered.set();row['children']=[{'path':path+'/child','bytes':1}];await release.wait();row.update(bytes=1,status='success')
            with patch.object(scans,'scan',scan):
                await scans.start();await entered.wait()
                self.assertEqual(scans.snapshot()['status'],'running')
                self.assertEqual(len(scans.snapshot()['directories'][0]['children']),1)
                await scans.cancel();self.assertEqual(scans.snapshot()['status'],'cancelled')
                release.set();await scans.start(force=True);await scans.task
                self.assertEqual(scans.snapshot()['status'],'success')
                result=await scans.start();self.assertTrue(result['cached'])
                result=await scans.start(force=True);self.assertFalse(result['cached']);await scans.task
    async def test_comparison_preserves_complete_baseline(self):
        scans=ScanManager()
        def finish(size,status='success'):
            scans.state={'finished_at':str(size),'directories':[{'path':'/example','bytes':size,'status':status,'children':[{'path':'/example/child','bytes':size}]}]}
            scans.compare_completed()
            return scans.state['directories'][0]
        self.assertNotIn('delta_bytes',finish(100))
        self.assertNotIn('delta_bytes',finish(50,'partial'))
        row=finish(130)
        self.assertEqual(row['delta_bytes'],30)
        self.assertEqual(row['children'][0]['delta_bytes'],30)
        self.assertEqual(row['previous_scanned_at'],'100')
        self.assertEqual(finish(120)['delta_bytes'],-10)

    async def test_reveal_validates_path_and_uses_argument_list(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as root:
            scans=ScanManager([root])
            with patch.object(app,'scans',scans), patch.object(app,'run_command',new_callable=AsyncMock,return_value=(0,'','')) as command:
                await app.reveal_storage(app.RevealRequest(path=root))
                command.assert_awaited_once_with(['/usr/bin/open','-R',str(Path(root).resolve())],timeout=5)
                with self.assertRaises(app.HTTPException):
                    await app.reveal_storage(app.RevealRequest(path='/'))
                self.assertEqual(command.await_count,1)

    async def test_small_real_scan(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'child').mkdir();Path(root,'child','example').write_text('test')
            scans=ScanManager([root]);await scans.start();await scans.task
            state=scans.snapshot();self.assertEqual(state['status'],'success')
            self.assertGreater(state['directories'][0]['bytes'],0)
            self.assertEqual(len(state['directories'][0]['children']),1)


class LogLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_eof_closes_socket_and_reaps_stream(self):
        proc=Mock(returncode=0,pid=999999)
        proc.stdout=Mock();proc.stdout.readline=AsyncMock(return_value=b'')
        proc.stderr=Mock();proc.stderr.read=AsyncMock(return_value=b'')
        proc.communicate=AsyncMock(return_value=(b'',b''))
        ws=Mock();ws.accept=AsyncMock();ws.send_text=AsyncMock();ws.close=AsyncMock()
        async def receive():await asyncio.Event().wait()
        ws.receive_text=receive
        with patch.object(app.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)),patch.object(app,'log_clients',0),patch.object(app,'_terminate_process_group') as stop:
            await asyncio.wait_for(app.logs_websocket(ws),1)
            self.assertEqual(app.log_clients,0)
            stop.assert_called_once_with(proc)
        ws.close.assert_awaited_once();proc.communicate.assert_awaited_once()
    async def test_disconnect_cancels_pump(self):
        proc=Mock(returncode=0,pid=999999)
        async def blocked():await asyncio.Event().wait()
        proc.stdout=Mock();proc.stdout.readline=blocked
        proc.stderr=Mock();proc.stderr.read=AsyncMock(return_value=b'')
        proc.communicate=AsyncMock(return_value=(b'',b''))
        ws=Mock();ws.accept=AsyncMock();ws.send_text=AsyncMock();ws.close=AsyncMock();ws.receive_text=AsyncMock(side_effect=app.WebSocketDisconnect())
        with patch.object(app.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)),patch.object(app,'log_clients',0),patch.object(app,'_terminate_process_group'):
            await asyncio.wait_for(app.logs_websocket(ws),1)
            self.assertEqual(app.log_clients,0)
        ws.close.assert_awaited_once()

class ResourceLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_are_bounded(self):
        release=asyncio.Event();four_started=asyncio.Event();active=0;peak=0
        async def work(args,timeout):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            if active==4:four_started.set()
            await release.wait();active-=1
            return 0,'',''
        with patch.object(app,'command_slots',asyncio.Semaphore(4)),patch.object(app,'_run_command',work):
            tasks=[asyncio.create_task(app.run_command(['fixture'],1)) for _ in range(8)]
            await asyncio.wait_for(four_started.wait(),1)
            self.assertEqual(active,4)
            release.set();await asyncio.gather(*tasks)
        self.assertEqual(peak,4)

    async def test_telemetry_connection_limit(self):
        hub=TelemetryHub()
        for _ in range(32):hub.subscribe()
        ws=Mock();ws.accept=AsyncMock();ws.close=AsyncMock()
        with patch.object(app,'hub',hub):await app.websocket_endpoint(ws)
        ws.accept.assert_not_called();ws.close.assert_awaited_once_with(code=1013)

if __name__=='__main__':unittest.main()
