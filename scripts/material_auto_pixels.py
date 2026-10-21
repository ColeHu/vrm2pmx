"""Host-Python texture worker for automatic VRM->PMX. Requires NumPy/Pillow."""
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image


def linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def srgb(rgb):
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1 / 2.4) - .055)


def sample_pixels(rgba, uv, offset=(0, 0), scale=(1, 1), rotation=0, wrap=(10497, 10497)):
    """RGBA top-down array; bottom-left UV. Supports repeat, clamp, mirrored repeat."""
    h, w = rgba.shape[:2]
    uv = np.asarray(uv, dtype=float)
    if uv.shape[-1] != 2 or not np.isfinite(uv).all():
        raise ValueError("Expected finite UV pairs")
    u, v = uv[..., 0] * scale[0], uv[..., 1] * scale[1]
    c, s = math.cos(rotation), math.sin(rotation)
    u, v = c*u-s*v+offset[0], s*u+c*v+offset[1]
    x, y = u*w-.5, (1-v)*h-.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x-x0, y-y0
    def index(i, size, mode):
        if mode == 10497:
            return i % size
        if mode == 33071:
            return np.clip(i, 0, size-1)
        if mode == 33648:
            j = i % (2*size)
            return np.where(j < size, j, 2*size-1-j)
        raise ValueError("Unsupported sampler wrap")
    def at(ix, iy):
        return rgba[index(iy, h, wrap[1]), index(ix, w, wrap[0])]
    return (at(x0,y0)*(1-fx)[...,None]*(1-fy)[...,None]
            +at(x0+1,y0)*fx[...,None]*(1-fy)[...,None]
            +at(x0,y0+1)*(1-fx)[...,None]*fy[...,None]
            +at(x0+1,y0+1)*fx[...,None]*fy[...,None])


def read_rgba(source):
    if Path(source).suffix.lower()=='.npy':
        rgba=np.load(source,allow_pickle=False).astype(np.float32)
        if rgba.ndim!=3 or rgba.shape[-1]!=4 or not np.isfinite(rgba).all():
            raise ValueError('Invalid staged RGBA buffer')
        return rgba
    return np.asarray(Image.open(source).convert('RGBA'),dtype=np.float32)/255


def transformed(source, info):
    rgba = read_rgba(source)
    # Filter colors in their declared space, not alpha through sRGB.
    if info.get("encoding", "srgb") == "srgb":
        rgba[:, :, :3] = linear(rgba[:, :, :3])
    if (list(info.get('offset',(0,0)))==[0,0] and list(info.get('scale',(1,1)))==[1,1]
            and abs(info.get('rotation',0))<1e-8):
        return rgba
    w, h = rgba.shape[1], rgba.shape[0]
    u, v = np.meshgrid((np.arange(w)+.5)/w, 1-(np.arange(h)+.5)/h)
    return sample_pixels(rgba, np.stack((u,v),axis=-1), info.get("offset",(0,0)),
                         info.get("scale",(1,1)), info.get("rotation",0), info.get("wrap",(10497,10497)))


def save_png(path, values, warnings, label):
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite output pixels")
    if np.any(values < 0) or np.any(values > 1):
        warnings.append(label + ": LDR clipping approximation")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.uint8(np.rint(np.clip(values, 0, 1)*255))).save(path, format="PNG")


def convert_row(row, outdir):
    out = Path(outdir)
    warnings = list(row.get("warnings", []))
    tex = row["textures"]
    factor = np.array(row["base_factor"], dtype=float)
    if tex["base"].get("path"):
        base = transformed(tex["base"]["path"], tex["base"])
    else:
        base = np.ones((4,4,4), dtype=float)
    base[:, :, :3] *= factor[:3]
    if row.get('base_alpha_used') is False:
        base[:,:,3]=1
    alpha_tex=tex.get('alpha',{})
    if alpha_tex.get('path'):
        mask_pixels=transformed(alpha_tex['path'],alpha_tex)
        if alpha_tex['channel']=='alpha':
            mask=mask_pixels[:,:,3]
        else:
            if np.max(np.abs(mask_pixels[:,:,:3]-mask_pixels[:,:,:1]))>1/255:
                raise ValueError('Color-to-opacity image must be grayscale; supply an explicit RGB-to-value adapter')
            mask=mask_pixels[:,:,0]
        if mask.shape!=base.shape[:2]:
            w,h=base.shape[1],base.shape[0]
            u,v=np.meshgrid((np.arange(w)+.5)/w,1-(np.arange(h)+.5)/h)
            mask=sample_pixels(mask[...,None],np.stack((u,v),axis=-1))[:,:,0]
        if alpha_tex.get('invert'):
            mask=1-mask
        base[:,:,3]*=mask
    mode = row["alpha_mode"]
    if mode == "OPAQUE":
        base[:, :, 3] = 1
    elif mode == "MASK":
        base[:, :, 3] = (base[:, :, 3]*factor[3] >= row["alpha_cutoff"]).astype(float)
    elif mode != "BLEND":
        raise ValueError("Unsupported alpha mode")
    output_base = base.copy()
    output_base[:, :, :3] = srgb(base[:, :, :3])
    path = out / (row["key"]+"_base.png")
    save_png(path, output_base, warnings, "base")
    result = dict(base=str(path.resolve()), toon=None, sphere=None,
                  material_alpha=float(factor[3]) if mode == "BLEND" else 1.0)
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


def run(request):
    out = Path(request["output_dir"]).resolve()
    out.mkdir(parents=True,exist_ok=True)
    return {row["key"]: convert_row(row,out) for row in request["materials"]}


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    Path(sys.argv[2]).write_text(json.dumps(run(request),ensure_ascii=False,indent=2),encoding="utf-8")
