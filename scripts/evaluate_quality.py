"""Compare depth processing variants on real model outputs; no ground-truth claim."""
import json
from pathlib import Path

import cv2
import numpy as np

from arc_sbs.depth import DepthEstimator, DepthNormalizer
from arc_sbs.gpu_stereo import OpenCLRenderer

estimator=DepthEstimator('models/depth-small.xml')
renderer=OpenCLRenderer()
normalizers={mode:DepthNormalizer(sampled=mode=='sampled') for mode in ('full','sampled','model')}
cap=cv2.VideoCapture('artifacts/feasibility-source-1080p.mp4')
frames=[]
for idx in (0,40,80,120,160,200):
    cap.set(cv2.CAP_PROP_POS_FRAMES,idx)
    ok,frame=cap.read()
    if ok:
        frames.append(('procedural',idx,frame))
cap.release()
natural=cv2.imread('artifacts/natural.jpg')
if natural is None:
    raise SystemExit('Fetch artifacts/natural.jpg as documented in README.md')
# Natural camera-pan approximation; these are transformed stills, not live film.
natural=cv2.resize(natural,(2200,1464))
for idx,x in enumerate((0,50,100,150,200,280)):
    frames.append(('natural_pan',idx,natural[100:1180,x:x+1920].copy()))
metrics=[]
for scene,index,frame in frames:
    h,w=frame.shape[:2]
    native=estimator.infer_native(frame)
    raw=cv2.resize(native,(w,h),interpolation=cv2.INTER_CUBIC)
    depths={}
    for mode,norm in normalizers.items():
        value=norm.apply(native if mode=='model' else raw,frame)
        if mode=='model':
            value=cv2.resize(value,(w,h),interpolation=cv2.INTER_CUBIC)
            np.clip(value,0,1,out=value)
        depths[mode]=value
    reference=renderer.render(frame,depths['full'])
    comparisons={}
    for mode in ('sampled','model'):
        delta=np.abs(depths[mode]-depths['full'])
        output=renderer.render(frame,depths[mode])
        color=np.abs(output.astype(np.int16)-reference.astype(np.int16))
        comparisons[mode]={'depth_mae':float(delta.mean()),'depth_p95':float(np.percentile(delta,95)),
             'disparity_p99_px':float(np.percentile(delta,99)*0.025*w),
             'disparity_max_px':float(delta.max()*0.025*w),
             'disparity_mae_px':float(delta.mean()*0.025*w),
             'disparity_p95_px':float(np.percentile(delta,95)*0.025*w),
             'rgb_mae_8bit':float(color.mean()),
             'changed_pixel_fraction':float(np.mean(np.any(color!=0,axis=2)))}
    metrics.append({'scene':scene,'frame':index,'comparisons':comparisons})
    if scene=='natural_pan' and index==2:
        # Equal-sized thumbnails: RGB, reference depth, sampled delta, model delta.
        views=[frame,cv2.applyColorMap(np.uint8(depths['full']*255),cv2.COLORMAP_INFERNO)]
        for mode in ('sampled','model'):
            error=np.clip(np.abs(depths[mode]-depths['full'])*20,0,1)
            views.append(cv2.applyColorMap(np.uint8(error*255),cv2.COLORMAP_INFERNO))
        thumbs=[cv2.resize(v,(640,360)) for v in views]
        cv2.imwrite('artifacts/feasibility/depth-quality.jpg',np.concatenate((np.concatenate(thumbs[:2],axis=1),np.concatenate(thumbs[2:],axis=1)),axis=0))
summary=[]
for scene in ('procedural','natural_pan'):
    for mode in ('sampled','model'):
        selected=[r['comparisons'][mode] for r in metrics if r['scene']==scene]
        summary.append({'scene':scene,'mode':mode,
            **{k:float(np.mean([r[k] for r in selected])) for k in selected[0]}})
result={'scope':'12 transformed/synthetic frames, same real model output; differences versus full-resolution reference, not ground truth',
        'model_shape':[estimator.height,estimator.width], 'input_size':[1920,1080],
        'summary':summary,'frames':metrics}
Path('artifacts/feasibility/quality.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(summary,indent=2))
