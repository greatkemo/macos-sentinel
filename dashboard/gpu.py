"""Best-effort, unprivileged IOAccelerator driver counters; never invent usage."""
import math
import plistlib
import subprocess
from datetime import datetime, timezone

def percentage(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        return None
    return round(value,1) if math.isfinite(value) and 0<=value<=100 else None

def parse_gpu_registry(registry):
    devices=[]
    pending=[registry]
    while pending:
        item=pending.pop()
        if isinstance(item,list):
            pending.extend(reversed(item))
        elif isinstance(item,dict):
            stats=item.get('PerformanceStatistics')
            if isinstance(stats,dict):
                used=stats.get('In use system memory')
                devices.append({'name':f'GPU {len(devices)+1}',
                    'percent':percentage(stats.get('Device Utilization %')),
                    'renderer_percent':percentage(stats.get('Renderer Utilization %')),
                    'tiler_percent':percentage(stats.get('Tiler Utilization %')),
                    'shared_memory_bytes':used if isinstance(used,int) and not isinstance(used,bool) and used>=0 else None})
                if len(devices)>=16:break
            pending.extend(reversed(item.get('IORegistryEntryChildren',[])))
    percent=devices[0]['percent'] if len(devices)==1 else None
    return {'status':'success' if percent is not None else ('partial' if devices else 'unavailable'),
            'percent':percent,'devices':devices,'source':'IOAccelerator PerformanceStatistics',
            'reason':None if percent is not None else ('Multiple/unsupported GPU counters; see device readings' if devices else 'GPU utilization counters are not exposed by this driver')}

def collect_gpu():
    try:
        raw=subprocess.check_output(['/usr/sbin/ioreg','-a','-r','-c','IOAccelerator'],stderr=subprocess.DEVNULL,timeout=2)
        if len(raw)>4*1024*1024:
            raise ValueError('GPU registry output exceeded size limit')
        result=parse_gpu_registry(plistlib.loads(raw))
    except (OSError,subprocess.SubprocessError,ValueError,plistlib.InvalidFileException):
        result={'status':'unavailable','percent':None,'devices':[], 'source':'IOAccelerator PerformanceStatistics','reason':'GPU counters unavailable, restricted or timed out'}
    result['checked_at']=datetime.now(timezone.utc).isoformat()
    return result
