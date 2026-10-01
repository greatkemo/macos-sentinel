import unittest
from unittest.mock import Mock,patch
from dashboard import collectors
from dashboard.gpu import parse_gpu_registry,collect_gpu
from dashboard.runtime import TelemetryHub

class MemoryTests(unittest.TestCase):
    def test_used_and_percent_share_definition_with_compression(self):
        vm=Mock(total=64*2**30,used=27*2**30,free=3*2**30,available=25*2**30,percent=60.9,active=21*2**30,wired=6*2**30,inactive=22*2**30)
        with patch.object(collectors.psutil,'virtual_memory',return_value=vm),patch.object(collectors.psutil,'swap_memory'),patch.object(collectors,'collect_pressure',return_value={}),patch.object(collectors.subprocess,'check_output',side_effect=OSError('unavailable')):
            result=collectors.collect_memory(lambda *args:None)
        self.assertEqual(result['used'],39*2**30)
        self.assertEqual(result['percent'],round(result['used']/result['total']*100,1))
        self.assertEqual(result['breakdown_status'],'partial')

class CapacityTests(unittest.TestCase):
    def test_available_and_physical_capacity_are_separate(self):
        result=collectors.storage_metrics(994662584320,60410449920,157592081309,906612445184)
        self.assertEqual(result['used'],837070503011)
        self.assertEqual(result['reclaimable'],97181631389)
        self.assertEqual(result['used']+result['available'],result['total'])
        self.assertEqual(result['percent'],84.2)
        self.assertEqual(result['status'],'success')
    def test_no_available_estimate_is_not_reported_as_zero(self):
        result=collectors.storage_metrics(1000,60)
        self.assertIsNone(result['available']);self.assertIsNone(result['reclaimable'])
        self.assertEqual(result['used'],940);self.assertEqual(result['basis'],'physical_free')
    def test_inconsistent_capacity_rejected(self):
        for amount in (-1,50,1001,True):
            self.assertEqual(collectors.storage_metrics(1000,60,amount)['status'],'partial')
    def test_disk_uses_foundation_instead_of_data_volume_used(self):
        with patch.object(collectors.psutil,'disk_usage',return_value=Mock(total=1000,used=900,free=60)),patch.object(collectors,'volume_capacity',return_value={'total':1000,'free':60,'available':160}):
            result=collectors.collect_disk(lambda *args:None)
        self.assertEqual(result['used'],840);self.assertEqual(result['percent'],84)
    def test_native_failure_preserves_honest_fallback(self):
        with patch.object(collectors.psutil,'disk_usage',return_value=Mock(total=1000,used=900,free=60)),patch.object(collectors,'volume_capacity',side_effect=OSError('unsupported')):
            result=collectors.collect_disk(lambda *args:None)
        self.assertEqual(result['used'],940);self.assertIsNone(result['available'])

class GPUTests(unittest.TestCase):
    def test_real_driver_shape(self):
        result=parse_gpu_registry([{'IORegistryEntryChildren':[{'PerformanceStatistics':{'Device Utilization %':26,'Renderer Utilization %':25,'Tiler Utilization %':26,'In use system memory':2500000000}}]}])
        self.assertEqual(result['percent'],26)
        self.assertEqual(result['devices'][0]['shared_memory_bytes'],2500000000)
    def test_idle_zero_is_available(self):
        self.assertEqual(parse_gpu_registry([{'PerformanceStatistics':{'Device Utilization %':0}}])['status'],'success')
    def test_unsupported_and_invalid_metrics_are_not_zero(self):
        self.assertIsNone(parse_gpu_registry([])['percent'])
        for value in (None,-1,101,float('nan'),True,'20'):
            result=parse_gpu_registry([{'PerformanceStatistics':{'Device Utilization %':value}}])
            self.assertIsNone(result['percent'])
    def test_multiple_devices_are_not_summed(self):
        result=parse_gpu_registry([{'PerformanceStatistics':{'Device Utilization %':80}},{'PerformanceStatistics':{'Device Utilization %':70}}])
        self.assertIsNone(result['percent']);self.assertEqual(len(result['devices']),2)
    def test_command_failure_unavailable(self):
        with patch('dashboard.gpu.subprocess.check_output',side_effect=OSError('missing')):
            self.assertEqual(collect_gpu()['status'],'unavailable')
    def test_history_allowlists_gpu_value(self):
        p={'timestamp':'2026-10-01T12:00:00+00:00','cpu':{'percent':1},'memory':{'percent':2},'network':{'download_bps':0,'upload_bps':0},'gpu':{'percent':23,'devices':[{'private':'secret'}]}}
        result=TelemetryHub.aggregate(p)
        self.assertEqual(result['gpu_percent'],23);self.assertNotIn('devices',result)
