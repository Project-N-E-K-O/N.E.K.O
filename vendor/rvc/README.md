# Vendored RVC (N.E.K.O local copy)

This directory is a **copy** from your RVC install (default `D:\RVC`).
The original folder is **never modified** by N.E.K.O.

## Inference (缈诲敱鎻掍欢)

Used by `plugin/plugins/rvc_cover` via `[rvc].rvc_root = vendor/rvc`.

## Voice training (澹伴煶璁粌)

Gradio UI (same as the original pack):

```bat
scripts\start_rvc_training.bat
```

Or:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_rvc_training.ps1
```

Training writes under this folder only (`logs/`, `assets/weights/`).
After training, export / copy the `.pth` into `assets/weights` so the cover plugin can use it:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sync_rvc_weights.ps1
```

## Refresh from source

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
# slim infer-only:
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1 -SkipTraining
# include UVR tab assets:
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1 -IncludeUvr
```
