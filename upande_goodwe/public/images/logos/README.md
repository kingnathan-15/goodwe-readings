# Logo files for the Solar & Water app mark

| File | Role |
|---|---|
| `upande.svg` | The Upande arrow (from upande_production's `upande_mark.svg`). Shown first and at rest. |
| `goodwe.png` | GoodWe logo, shown second. Cleaned from the supplied file (baked-in checkerboard removed, cropped). Only 189 px wide; replace with an official SVG or larger PNG if one is available. |
| `hydrax.png` | HydraX Water logo, shown third. Cropped and compressed from the supplied file. |

`goodwe.svg` / `hydrax.svg` are picked up instead of the PNGs if added. A missing partner file is skipped.

Rebuild the mark after changing any file:

```bash
python -m upande_goodwe.goodwe.branding        # from apps/upande_goodwe
bench build --app upande_goodwe
```
