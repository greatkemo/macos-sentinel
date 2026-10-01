"""Deterministic local browser fixture. Never runs native diagnostic commands."""
import asyncio
import copy
from datetime import datetime, timezone
import app
import tempfile
from dashboard.sessions import SessionStore
_fixture_data = tempfile.TemporaryDirectory(prefix="sentinel-fixture-")
app.sessions = SessionStore(_fixture_data.name)

class FixtureSampler:
    def prime(self):pass
    def snapshot(self):
        processes=[{'pid':100+i,'ppid':0,'name':f'Example {i}','application':'Example.app' if i<4 else f'Example {i}', 'create_time':1000+i,'cpu_percent':i%10,'memory_percent':(i%7)/10} for i in range(135)]
        return {'timestamp':datetime.now(timezone.utc).isoformat(),
                'host':{'hostname':'Test Mac','model':'Apple test CPU','os':'macOS fixture 1.0 (TEST)','cpu_count':4,'uptime_seconds':1000,'machine_name':'Test Mac','marketing_year':'2024','chip':'Apple test CPU','memory':'16 GB','startup_disk':'Macintosh HD','serial_number':'TESTSERIAL01','os_product':'macOS','os_version':'1.0','os_build':'TEST','architecture':'arm64','product_image_available':False},
                'topology':{'performance_cores':2,'efficiency_cores':2},'cpu':{'percent':25,'per_core':[10,20,30,40]},
                'memory':{'total':16000000000,'used':8000000000,'percent':50,'free':2000000000,'available':8000000000,'wired':2000000000,'active':4000000000,'compressed':2000000000,'vm_free':2000000000,'inactive':4000000000,'speculative':1000000000,'other':1000000000,'swap':{'used':0,'total':1000000000},'pressure':{'status':'success','level':'Normal'}},
                'gpu':{'status':'success','percent':23,'devices':[{'name':'GPU 1','percent':23,'renderer_percent':22,'tiler_percent':23,'shared_memory_bytes':2000000000}]},'disk':{'status':'success','mount':'/System/Volumes/Data','total':994662584320,'used':837070503011,'free':60410449920,'available':157592081309,'reclaimable':97181631389,'percent':84.2},
                'disk_io':{'read_bps':1024,'write_bps':2048},'battery':{'present':False,'source':'AC','power_state':'AC'},
                'network':{'download_bps':4096,'upload_bps':2048,'interfaces':[{'name':'en0','download_bps':4096,'upload_bps':2048}]},
                'processes':processes,'process_tree':processes}
app.TelemetrySampler=FixtureSampler
app.collect_apfs=lambda:{'ok':True,'status':'success','containers':[{'device':'disk-test','size_bytes':10000000000,'used_bytes':6000000000,'free_bytes':4000000000,'volumes':[]}],'mounts':[]}
app.collect_system_info=lambda:{'ok':True,'checked_at':datetime.now(timezone.utc).isoformat(),'hardware':{'name':'Mac','fields':[{'key':'Model','value':'Fixture'}]},'software':{'fields':[]},'applications':[{'name':'Example Editor','path':'/Applications/Example.app','version':'1'},{'name':'Other App','path':'/Applications/Other.app','version':'2'}]}
async def fake_scan(path,row):
    row['children']=[{'path':path+'/Example','bytes':2048,'depth':1}]
    await asyncio.sleep(2)
    row.update(bytes=4096,status='partial',error='Fixture: one inaccessible directory')
app.scans.scan=fake_scan
async def fake_command(args,timeout):
    if args[0]=='softwareupdate':return 0,'Unexpected fixture output',''
    if args[0]=='networkQuality':return 0,'{"dl_throughput":100000000,"ul_throughput":20000000,"responsiveness":500}',''
    return 0,'',''
app.run_command=fake_command
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app.app,host='127.0.0.1',port=app.PORT,log_level='warning')
