"""Measure first decoded output while conversion is in progress, headlessly.

Uses only loopback UDP and FFmpeg's null output; does not open a headset window.
uv run python scripts/evaluate_stream.py --mode full --pipeline overlap
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import threading
import time

from arc_sbs.video import convert,probe

p=argparse.ArgumentParser()
p.add_argument('--source',type=Path,default=Path('artifacts/feasibility-source-1080p.mp4'))
p.add_argument('--mode',choices=['full','sampled','model'],default='full')
p.add_argument('--pipeline',choices=['serial','overlap'],default='overlap')
p.add_argument('--frames',type=int,default=144)
p.add_argument('--pace',action='store_true')
p.add_argument('--tag',default='stream')
a=p.parse_args()
root=Path('artifacts/feasibility')
root.mkdir(parents=True,exist_ok=True)
output=root/(a.tag+'.mp4')
report=root/(a.tag+'.json')
if output.exists() or report.exists():
    raise SystemExit('Choose a fresh --tag')
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as port_probe:
    port_probe.bind(('127.0.0.1',0));port=port_probe.getsockname()[1]
# Port selection has the ordinary short bind race; consumer starts before producer.
log=(root/(a.tag+'-receiver.log')).open('w+b')
receiver=subprocess.Popen(['ffmpeg','-hide_banner','-nostdin','-loglevel','info',
    '-stats_period','0.05','-progress','pipe:1','-probesize','32768','-analyzeduration','0',
    '-flags','low_delay','-i',f'udp://127.0.0.1:{port}?fifo_size=16384&overrun_nonfatal=1&timeout=4000000',
    '-map','0:v:0','-map','0:a:0?','-f','null','-'],stdout=subprocess.PIPE,stderr=log,text=True)
observed={'first_frame_monotonic':None,'decoded_frames':0,'last_media_us':0}
ready=threading.Event()
def watch():
    for line in receiver.stdout:
        key,sep,value=line.strip().partition('=')
        if key=='frame' and sep:
            observed['decoded_frames']=int(value)
            if int(value)>0 and observed['first_frame_monotonic'] is None:
                observed['first_frame_monotonic']=time.perf_counter();ready.set()
        if key=='out_time_us' and value.lstrip('-').isdigit():
            observed['last_media_us']=int(value)
reader=threading.Thread(target=watch,daemon=True);reader.start()
def xr_snapshot():
    path=Path('/run/user/1000/wivrn-pro/performance.json')
    if not path.exists():
        return None
    data=json.loads(path.read_text())
    return {'target_hz':data.get('target_hz'),
            'streams':[{k:r.get(k) for k in ('id','frames','presented_frames','encode_ms','delivery_ms')}
                       for r in data.get('streams',[])]}
xr_before=xr_snapshot()
started=time.perf_counter()
try:
    conversion=convert(a.source,output,
        model='models/depth-small.xml',frames=a.frames,depth_processing=a.mode,
        pipeline=a.pipeline,preview_port=port,pace=a.pace)
    finished=time.perf_counter()
    ready.wait(timeout=2)
finally:
    # UDP has no EOF. Allow the idle timeout to flush delayed decoder frames.
    try:
        receiver.wait(timeout=5)
    except subprocess.TimeoutExpired:
        receiver.terminate()
        try:
            receiver.wait(timeout=2)
        except subprocess.TimeoutExpired:
            receiver.kill();receiver.wait()
    reader.join(timeout=1)
    log.flush();log.seek(0);messages=log.read().decode(errors='replace');log.close()
first=observed['first_frame_monotonic']
metadata=probe(output)
result={'conversion':conversion,'receiver':'FFmpeg software decode to null output; no visual/XR latency measurement',
        'first_decoded_progress_seconds':first-started if first else None,
        'conversion_wall_seconds_including_setup':finished-started,
        'decoded_before_conversion_finished':first is not None and first<finished,
        'decoded_frames':observed['decoded_frames'],'last_media_seconds':observed['last_media_us']/1e6,
        'receiver_detected_audio':'Audio: aac' in messages,
        'receiver_errors':[line for line in messages.splitlines() if any(t in line.lower() for t in ('error while','invalid data','packet corrupt','circular buffer overrun'))],
        'archive_duration':float(metadata['format']['duration']),
        'xr_before':xr_before,'xr_after':xr_snapshot()}
report.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
if not result['decoded_before_conversion_finished'] or not result['receiver_detected_audio'] or result['receiver_errors']:
    raise SystemExit(1)
