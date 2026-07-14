#!/usr/bin/env python3
"""Print or open an official Neuroglancer URL for the local comparison layers."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse


def image_layer(name: str, shader: str, opacity: float = 0.55) -> dict:
    return {
        "type": "image",
        "source": f"precomputed://http://localhost:8085/{name}",
        "shader": shader,
        "opacity": opacity,
        "blend": "additive",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    gray = "void main() { float v = toNormalized(getDataValue()); emitRGB(vec3(v)); }"
    red = "void main() { float v = toNormalized(getDataValue()); emitRGB(vec3(v, 0.0, 0.0)); }"
    green = "void main() { float v = toNormalized(getDataValue()); emitRGB(vec3(0.0, v, 0.0)); }"
    blue = "void main() { float v = toNormalized(getDataValue()); emitRGB(vec3(0.0, 0.0, v)); }"
    state = {
        "dimensions": {"x": [2.5e-5, "m"], "y": [2.5e-5, "m"], "z": [2.5e-5, "m"]},
        "position": [232.5, 113, 275.5],
        "crossSectionScale": 2.2,
        "projectionScale": 3800,
        "layers": [
            {"name": "Original LSFM (gray)", **image_layer("original", gray, 0.65)},
            {"name": "Elastix (red)", **image_layer("elastix", red)},
            {"name": "ANTs (green)", **image_layer("ants", green)},
            {"name": "LDDMM (blue)", **image_layer("lddmm", blue)},
        ],
        "layout": "4panel",
        "selectedLayer": {"layer": "Original LSFM (gray)", "visible": True},
    }
    encoded = urllib.parse.quote(json.dumps(state, separators=(",", ":")), safe="")
    url = "https://neuroglancer-demo.appspot.com/#!" + encoded
    print(url)
    if not args.no_open:
        subprocess.run(["open", url], check=True)


if __name__ == "__main__":
    main()
