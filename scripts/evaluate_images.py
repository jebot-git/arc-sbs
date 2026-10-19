"""Measure still-image conversion plus Qt image preparation, without video codecs.

QT_QPA_PLATFORM=offscreen uv run --extra gui python scripts/evaluate_images.py
This is not a headset display or camera-capture latency test.
"""
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PySide6.QtGui import QImage

from arc_sbs.depth import DepthEstimator,DepthNormalizer
from arc_sbs.gpu_stereo import OpenCLRenderer

source_path=Path('artifacts/natural.jpg')
t=time.perf_counter()
source=cv2.imread(str(source_path))
read_seconds=time.perf_counter()-t
if source is None:
    raise SystemExit('Fetch artifacts/natural.jpg as described in README.md')
h,w=source.shape[:2]
ch=min(h,round(w*9/16));cw=min(w,round(h*16/9))
source=source[(h-ch)//2:(h+ch)//2,(w-cw)//2:(w+cw)//2]
t=time.perf_counter()
estimator=DepthEstimator('models/depth-small.xml')
renderer=OpenCLRenderer()
startup=time.perf_counter()-t
cases=[]
for width,height in [(1920,1080),(3840,2160)]:
    frame=cv2.resize(source,(width,height),interpolation=cv2.INTER_AREA)
    for layout in ('half','full'):
        for mode in ('full','sampled'):
            rows=[]
            for repeat in range(23):
                # Independent still images should not inherit video EMA history.
                normalizer=DepthNormalizer(sampled=mode=='sampled')
                start=time.perf_counter()
                raw=estimator.infer(frame)
                inferred=time.perf_counter()
                depth=normalizer.apply(raw,frame)
                normalized=time.perf_counter()
                sbs=renderer.render(frame,depth,layout=layout)
                rendered=time.perf_counter()
                image=QImage(sbs.data,sbs.shape[1],sbs.shape[0],sbs.strides[0],QImage.Format.Format_BGR888).copy()
                finished=time.perf_counter()
                assert not image.isNull()
                if repeat>=3:
                    rows.append([(inferred-start)*1000,(normalized-inferred)*1000,
                        (rendered-normalized)*1000,(finished-rendered)*1000,(finished-start)*1000])
            values=np.asarray(rows)
            result={'input_size':[width,height],'layout':layout,'depth_processing':mode,
                    'iterations':len(rows),
                    'mean_ms':dict(zip(['inference','normalization','stereo','qt_image_copy','total'],values.mean(axis=0).tolist())),
                    'total_p50_ms':float(np.median(values[:,-1])),
                    'total_p95_ms':float(np.percentile(values[:,-1],95)),
                    'equivalent_serial_fps':float(1000/values[:,-1].mean())}
            cases.append(result)
            print(f'{width}x{height} {layout} {mode}: {result["mean_ms"]["total"]:.1f} ms, p95 {result["total_p95_ms"]:.1f}',flush=True)
report={'scope':'Preloaded BGR image -> depth -> SBS -> owned Qt QImage. No file write, encoder, audio, actual Qt window presentation, headset upload or camera capture. Three warmups excluded.',
    'device':estimator.device_name,'execution_devices':estimator.execution_devices,
    'model_shape':[estimator.height,estimator.width], 'startup_seconds':startup,
    'openvino_compile_seconds':estimator.compile_seconds,'source_jpeg_read_seconds':read_seconds,
    'cases':cases}
Path('artifacts/image-feasibility.json').write_text(json.dumps(report,indent=2)+'\n')
