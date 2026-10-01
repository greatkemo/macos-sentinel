"""Read Foundation volume capacity keys through their CoreFoundation bridge.

No compiler, subprocess, elevated privileges or third-party Python bridge needed.
Returned objects follow CoreFoundation's Copy/Create ownership rules.
"""
import ctypes as c
import platform
from functools import lru_cache

@lru_cache(maxsize=1)
def _frameworks():
    if platform.system() != 'Darwin':
        raise OSError('Foundation capacity is only available on macOS')
    cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    foundation=c.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
    signatures={
        'CFStringCreateWithCString':([c.c_void_p,c.c_char_p,c.c_uint32],c.c_void_p),
        'CFURLCreateWithFileSystemPath':([c.c_void_p,c.c_void_p,c.c_int,c.c_bool],c.c_void_p),
        'CFURLCopyResourcePropertyForKey':([c.c_void_p,c.c_void_p,c.POINTER(c.c_void_p),c.POINTER(c.c_void_p)],c.c_bool),
        'CFNumberGetValue':([c.c_void_p,c.c_int,c.c_void_p],c.c_bool),
        'CFNumberGetTypeID':([],c.c_ulong),
        'CFGetTypeID':([c.c_void_p],c.c_ulong),
        'CFRelease':([c.c_void_p],None),
    }
    for name,(args,result) in signatures.items():
        fn=getattr(cf,name);fn.argtypes=args;fn.restype=result
    return cf,foundation

def volume_capacity(path):
    cf,foundation=_frameworks()
    text=cf.CFStringCreateWithCString(None,path.encode('utf-8'),0x08000100)
    if not text:
        raise OSError('Could not create volume path')
    url=None
    try:
        url=cf.CFURLCreateWithFileSystemPath(None,text,0,True)
        if not url:
            raise OSError('Could not create volume URL')
        result={}
        for field,symbol in [('total','NSURLVolumeTotalCapacityKey'),('free','NSURLVolumeAvailableCapacityKey'),('available','NSURLVolumeAvailableCapacityForImportantUsageKey')]:
            value=c.c_void_p();error=c.c_void_p()
            try:
                key=c.c_void_p.in_dll(foundation,symbol).value
                ok=cf.CFURLCopyResourcePropertyForKey(url,key,c.byref(value),c.byref(error))
                number=c.c_longlong()
                if ok and value and cf.CFGetTypeID(value)==cf.CFNumberGetTypeID() and cf.CFNumberGetValue(value,4,c.byref(number)) and number.value>=0:
                    result[field]=number.value
            except ValueError:
                pass  # Key not exported on this macOS version.
            finally:
                if value:cf.CFRelease(value)
                if error:cf.CFRelease(error)
        return result
    finally:
        if url:cf.CFRelease(url)
        cf.CFRelease(text)
