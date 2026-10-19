"""Check decoded-frame parity across pipeline/decode implementations."""
import json
from pathlib import Path
import subprocess

root=Path('artifacts/feasibility')
cache={}
def hashes(name):
    if name not in cache:
        result=subprocess.run(['ffmpeg','-v','error','-i',str(root/(name+'.mp4')),
            '-map','0:v:0','-f','framemd5','-'],check=True,capture_output=True,text=True)
        if result.stderr:
            raise RuntimeError(result.stderr)
        cache[name]=[line.split(',')[-1].strip() for line in result.stdout.splitlines() if not line.startswith('#')]
    return cache[name]
checks=[]
for mode in ('full','sampled','model'):
    a,b=hashes(mode+'-serial-1'),hashes(mode+'-overlap-1')
    checks.append({'comparison':mode+' serial versus overlap','frames':len(a),
                   'identical':bool(a) and a==b})
a,b=hashes('full-overlap-1'),hashes('vaapi-planar-full-overlap-1')
checks.append({'comparison':'CPU versus VA-API source decode, full processing/overlap',
               'frames':len(a),'identical':bool(a) and a==b})
result={'checks':checks,'all_overlap_parity_passed':all(r['identical'] for r in checks[:3])}
(root/'output-validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
if not result['all_overlap_parity_passed']:
    raise SystemExit(1)
