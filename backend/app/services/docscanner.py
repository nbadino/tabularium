"""Optional DocScanner-L inference adapter.

DocScanner is kept out of the base environment because the upstream project
ships as a PyTorch/GPU research implementation.  Set ``TABULARIUM_DOCSCANNER_ROOT``
to a checkout of the official repository and provide ``seg.pth`` plus
``DocScanner-L.pth`` in its ``model_pretrained`` directory.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from PIL import Image

_model = None
_torch = None


def _root() -> Path:
    configured = os.environ.get("TABULARIUM_DOCSCANNER_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    # Checkout installato dallo script ufficiale di Tabularium.
    return Path(__file__).resolve().parents[3] / "vendor" / "DocScanner"


def available() -> bool:
    root = _root()
    weights = root / "model_pretrained"
    return (
        root.is_dir()
        and (weights / "seg.pth").is_file()
        and (weights / "DocScanner-L.pth").is_file()
    )


def _load():
    global _model, _torch
    if _model is not None:
        return _model, _torch
    if not available():
        return None, None
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
        import numpy as np
        import cv2
    except ImportError:
        return None, None

    root = _root().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        # Import the upstream modules, but keep the inference wrapper here so
        # weights can be loaded on CPU as well as CUDA.
        model_mod = importlib.import_module("model")
        seg_mod = importlib.import_module("seg")
        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.msk = seg_mod.U2NETP(3, 1)
                self.bm = model_mod.DocScanner()

            def forward(self, x):
                msk, *_ = self.msk(x)
                x = (msk > 0.5).float() * x
                bm = self.bm(x, iters=12, test_mode=True)
                return (2 * (bm / 286.8) - 1) * 0.99

        device_name = os.environ.get("TABULARIUM_DOCSCANNER_DEVICE", "cpu")
        device = torch.device(device_name if device_name.startswith("cuda") and torch.cuda.is_available() else "cpu")
        net = Net().to(device)
        seg_state = torch.load(root / "model_pretrained" / "seg.pth", map_location=device)
        rec_state = torch.load(root / "model_pretrained" / "DocScanner-L.pth", map_location=device)
        # The official segmentation checkpoint is saved with a `model.`
        # prefix; the rectification checkpoint is not.
        seg_state = {k[6:]: v for k, v in seg_state.items() if k[6:] in net.msk.state_dict()}
        rec_state = {k: v for k, v in rec_state.items() if k in net.bm.state_dict()}
        net.msk.load_state_dict(seg_state, strict=False)
        net.bm.load_state_dict(rec_state, strict=False)
        net.eval()
        _model, _torch = net, (torch, np, cv2, device, F)
        return _model, _torch
    except Exception:
        return None, None


def rectify(image: Image.Image) -> Image.Image | None:
    """Rectify one RGB page, returning the original canvas size."""
    net, runtime = _load()
    if net is None:
        return None
    torch, np, cv2, device, F = runtime
    source = image.convert("RGB")
    pad = max(32, int(min(source.size) * 0.08))
    padded = Image.new("RGB", (source.width + 2 * pad, source.height + 2 * pad), "white")
    padded.paste(source, (pad, pad))
    original = np.asarray(padded, dtype=np.float32) / 255.0
    h, w = original.shape[:2]
    small = cv2.resize(original, (288, 288)).transpose(2, 0, 1)
    tensor = torch.from_numpy(small).float().unsqueeze(0).to(device)
    with torch.inference_mode():
        bm = net(tensor)[0].detach().cpu().numpy()
    grid = np.stack([cv2.blur(cv2.resize(bm[0], (w, h)), (3, 3)), cv2.blur(cv2.resize(bm[1], (w, h)), (3, 3))], axis=2)
    inp = torch.from_numpy(original).permute(2, 0, 1).unsqueeze(0).float()
    out = F.grid_sample(inp, torch.from_numpy(grid).unsqueeze(0), align_corners=True)
    return Image.fromarray(np.clip(out[0].permute(1, 2, 0).cpu().numpy() * 255, 0, 255).astype("uint8"))
