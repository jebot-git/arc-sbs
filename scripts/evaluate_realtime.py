"""Sequential implementation matrix on one source; writes raw per-run reports.

uv run python scripts/evaluate_realtime.py artifacts/feasibility-source-1080p.mp4
All variants use the same model, source, renderer and encoder settings.
"""
import argparse
import json
from pathlib import Path
import statistics

from arc_sbs.video import convert, probe

p=argparse.ArgumentParser()
p.add_argument('source',type=Path)
p.add_argument('--output-dir',type=Path,default=Path('artifacts/feasibility'))
p.add_argument('--repeats',type=int,default=2)
p.add_argument('--frames',type=int,default=240)
a=p.parse_args()
if a.repeats < 1 or a.frames < 2:
    raise SystemExit('Positive repeats and at least two frames required')
a.output_dir.mkdir(parents=True,exist_ok=True)
cases=[(mode,pipeline) for mode in ('full','sampled','model') for pipeline in ('serial','overlap')]
runs=[]
for repeat in range(a.repeats):
    # Reverse the second sweep to reduce systematic warmup/order bias.
    for mode,pipeline in (cases if repeat%2==0 else cases[::-1]):
        stem=f'{mode}-{pipeline}-{repeat+1}'
        path=a.output_dir/(stem+'.mp4')
        report=a.output_dir/(stem+'.json')
        if path.exists() or report.exists():
            raise SystemExit(f'Refusing to replace {stem}; choose a new output directory')
        print(f'Running {stem}',flush=True)
        result=convert(a.source,path,model='models/depth-small.xml',frames=a.frames,
                       depth_processing=mode,pipeline=pipeline)
        metadata=probe(path)
        video=next(s for s in metadata['streams'] if s['codec_type']=='video')
        result['archive_validation']={'size':[video['width'],video['height']],
            'duration':float(metadata['format']['duration']),
            'audio_present':any(s['codec_type']=='audio' for s in metadata['streams'])}
        result['repeat']=repeat+1
        report.write_text(json.dumps(result,indent=2)+'\n')
        runs.append(result)
        print(f'{stem}: {result["pipeline_fps"]:.2f} fps, active {result["active_fps"]:.2f}',flush=True)
summary=[]
for mode,pipeline in cases:
    selected=[r for r in runs if r['depth_processing']==mode and r['pipeline']==pipeline]
    summary.append({'depth_processing':mode,'pipeline':pipeline,
        'pipeline_fps_mean':statistics.mean(r['pipeline_fps'] for r in selected),
        'pipeline_fps_min':min(r['pipeline_fps'] for r in selected),
        'pipeline_fps_max':max(r['pipeline_fps'] for r in selected),
        'active_fps_mean':statistics.mean(r['active_fps'] for r in selected),
        'normalization_ms_mean':statistics.mean(r['normalization_seconds']/r['frames']*1000 for r in selected),
        'inference_ms_mean':statistics.mean(r['inference_seconds']/r['frames']*1000 for r in selected),
        'render_ms_mean':statistics.mean(r['render_seconds']/r['frames']*1000 for r in selected)})
(a.output_dir/'summary.json').write_text(json.dumps({'source':str(a.source),'repeats':a.repeats,
    'frames_per_run':a.frames,'cases':summary},indent=2)+'\n')
print(json.dumps(summary,indent=2))
