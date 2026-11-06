"""Persistent OmniParser icon detector. Run using its isolated Python environment."""
from contextlib import redirect_stdout
import base64
import io
import json
import sys
from PIL import Image
with redirect_stdout(sys.stderr):
    from ultralytics import YOLO
    import torch
    torch.set_num_threads(4)

with redirect_stdout(sys.stderr):
    model = YOLO(sys.argv[1])
for line in sys.stdin:
    try:
        picture = Image.open(io.BytesIO(base64.b64decode(line))).convert('RGB')
        result = model.predict(picture, conf=.15, imgsz=1280, device='cpu', verbose=False)[0]
        icons = []
        for box, confidence in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()):
            x1, y1, x2, y2 = box
            icons.append(dict(type='icon', box=[x1, y1, x2-x1, y2-y1],
                              center=[(x1+x2)/2, (y1+y2)/2], confidence=confidence,
                              text='', clickable=None, description=None,
                              source='omniparser-icon-detect'))
        print(json.dumps(dict(items=icons)), flush=True)
    except Exception as exc:
        print(json.dumps(dict(error=str(exc))), flush=True)
