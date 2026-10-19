"""Diagnostic profiling of current host-copy GPU renderer; not zero-copy."""
import json
from pathlib import Path
import time
import numpy as np
import pyopencl as cl
from arc_sbs.gpu_stereo import OpenCLRenderer

h,w=1080,1920;n=h*w
r=OpenCLRenderer()
frame=np.random.default_rng(42).integers(0,256,(h,w,3),dtype=np.uint8)
depth=np.broadcast_to(np.linspace(0,1,w,dtype=np.float32),(h,w)).copy()
r.render(frame,depth)
queue=cl.CommandQueue(r.context,properties=cl.command_queue_properties.PROFILING_ENABLE)
output=np.empty_like(frame)
rows=[]
for repeat in range(23):
    start=time.perf_counter()
    events={}
    events['frame_upload']=cl.enqueue_copy(queue,r.frame,frame,is_blocking=False)
    events['depth_upload']=cl.enqueue_copy(queue,r.depth,depth,is_blocking=False)
    events['clear']=r.clear(queue,(2*n,),None,r.keys,np.int32(n))
    events['splat']=r.splat(queue,(n,2),None,r.depth,r.keys,np.int32(w),np.int32(n),np.float32(.025*w),np.float32(.5))
    events['pack']=r.pack(queue,(n,),None,r.frame,r.depth,r.keys,r.output,np.int32(w),np.int32(h),np.int32(1),np.int32(0))
    events['output_download']=cl.enqueue_copy(queue,output,r.output,is_blocking=True)
    wall=time.perf_counter()-start
    row={key:(event.profile.end-event.profile.start)/1e6 for key,event in events.items()}
    row['wall']=wall*1000
    if repeat>=3:
        rows.append(row)
result={'device':r.info,'iterations':len(rows),'size':[w,h],
        'mean_ms':{key:float(np.mean([r[key] for r in rows])) for key in rows[0]},
        'scope':'Profiling-enabled queue, existing buffers, three warmups. Device-event copy/kernel durations exclude Python and host enqueue overhead; wall includes those. Not an implementation of zero-copy.'}
Path('artifacts/feasibility/transfers.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
