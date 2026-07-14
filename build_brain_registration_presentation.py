#!/usr/bin/env python3
"""Build a short PowerPoint deck from the validated registration outputs."""

from __future__ import annotations

import colorsys
import json
import math
import shutil
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import imageio.v2 as imageio
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
import tifffile
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from scipy import ndimage
from skimage import filters, measure, morphology


ROOT = Path(__file__).resolve().parent
RUN_ID = "ssd_20260628_153426"
SCRATCH = Path("/Volumes/Seagate/scratch/Proj_reg_brain") / RUN_ID
FINAL = ROOT / "Wenxi" / "Proj_reg_brain" / RUN_ID
ASSETS = ROOT / "presentation_assets"
SLIDES = ASSETS / "slides"
OUT_PPTX = ROOT / "Brain_Registration_Project_Overview.pptx"
OUT_NOTES = ROOT / "Brain_Registration_Project_Speaker_Notes.md"
OUT_PREVIEW = ROOT / "Brain_Registration_Project_Preview.png"
OUT_INTERACTIVE = ROOT / "Brain_Registration_Interactive_3D.html"

SAMPLE = ROOT / "CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif"
TEMPLATE = ROOT / "CCFv3_25um.coronal.tif"
ATLAS = ROOT / "CCFv3_Atlas.ccf_2017.coronal.tif"

W, H = 1600, 900
EMU_W, EMU_H = 12192000, 6858000
BG = "#F5F7F8"
INK = "#172126"
MUTED = "#5E6A70"
TEAL = "#007C83"
CYAN = "#18A7B5"
CORAL = "#D45B4E"
GOLD = "#D7A43B"
GREEN = "#2D8A5F"
WHITE = "#FFFFFF"
BLACK = "#080B0D"

FONT_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_ITALIC = "/System/Library/Fonts/Supplemental/Arial Italic.ttf"


def font(size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_ITALIC if italic else FONT_REG
    return ImageFont.truetype(path, size)


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = word if not line else f"{line} {word}"
        if draw.textbbox((0, 0), candidate, font=fnt)[2] <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    fill: str,
    width: int,
    spacing: int = 8,
) -> int:
    x, y = xy
    line_height = fnt.size + spacing
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_height
    return y


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=12, fill=fill, outline=outline, width=2 if outline else 1)


def fit_image(base: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    fitted = ImageOps.fit(image.convert("RGB"), (x1 - x0, y1 - y0), method=Image.Resampling.LANCZOS)
    base.paste(fitted, (x0, y0))


def contain_image(base: Image.Image, image: Image.Image, box: tuple[int, int, int, int], fill: str = BG) -> None:
    x0, y0, x1, y1 = box
    canvas = Image.new("RGB", (x1 - x0, y1 - y0), rgb(fill))
    fitted = ImageOps.contain(image.convert("RGB"), canvas.size, method=Image.Resampling.LANCZOS)
    canvas.paste(fitted, ((canvas.width - fitted.width) // 2, (canvas.height - fitted.height) // 2))
    base.paste(canvas, (x0, y0))


def header(base: Image.Image, title: str, kicker: str, number: int, dark: bool = False) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(base)
    title_color = WHITE if dark else INK
    muted_color = "#C6D1D5" if dark else MUTED
    draw.text((70, 45), kicker.upper(), font=font(17, bold=True), fill=CYAN if dark else TEAL)
    draw.text((70, 78), title, font=font(38, bold=True), fill=title_color)
    draw.text((1510, 54), f"{number:02d}", font=font(18, bold=True), fill=muted_color, anchor="ra")
    return draw


def footer(draw: ImageDraw.ImageDraw, text: str, dark: bool = False) -> None:
    draw.line((70, 852, 1530, 852), fill="#314148" if dark else "#D7DEE1", width=1)
    draw.text((70, 865), text, font=font(14), fill="#AFC0C7" if dark else "#77858B")


def normalize(slice_: np.ndarray, lo: float = 1.0, hi: float = 99.5) -> np.ndarray:
    arr = np.asarray(slice_, dtype=np.float32)
    values = arr[arr > 0]
    if values.size == 0:
        return np.zeros_like(arr)
    a, b = np.percentile(values, [lo, hi])
    if b <= a:
        b = a + 1
    return np.clip((arr - a) / (b - a), 0, 1)


def overlay_rgb(sample: np.ndarray, warped: np.ndarray) -> np.ndarray:
    s = normalize(sample)
    w = normalize(warped)
    out = np.zeros((*s.shape, 3), dtype=np.float32)
    out[..., 0] = w
    out[..., 1] = s
    out[..., 2] = 0.50 * s + 0.65 * w
    return np.uint8(np.clip(out, 0, 1) * 255)


def label_rgb(labels: np.ndarray, background: np.ndarray | None = None) -> np.ndarray:
    labels = np.asarray(labels)
    out = np.zeros((*labels.shape, 3), dtype=np.float32)
    if background is not None:
        b = normalize(background)
        out[:] = b[..., None] * 0.30
    unique = np.unique(labels)
    unique = unique[unique != 0]
    for value in unique:
        hue = ((int(value) * 0.61803398875) % 1.0)
        color = colorsys.hsv_to_rgb(hue, 0.70, 0.95)
        out[labels == value] = color
    return np.uint8(np.clip(out, 0, 1) * 255)


def save_panel(image: np.ndarray, path: Path, title: str | None = None) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=160)
    ax.imshow(image)
    ax.axis("off")
    if title:
        ax.set_title(title, fontsize=14, fontweight="bold")
    fig.tight_layout(pad=0)
    fig.savefig(path, bbox_inches="tight", pad_inches=0, facecolor="black")
    plt.close(fig)


def mesh_trace(volume: np.ndarray, name: str, color: str, opacity: float) -> go.Mesh3d:
    smoothed = ndimage.gaussian_filter(volume.astype(np.float32), 1.0)
    positive = smoothed[smoothed > 0]
    level = float(filters.threshold_otsu(positive)) if positive.size else 0.1
    mask = smoothed >= level
    mask = morphology.remove_small_objects(mask, min_size=180)
    mask = morphology.binary_closing(mask, morphology.ball(1))
    mask = ndimage.binary_fill_holes(mask)
    vertices, faces, _, _ = measure.marching_cubes(mask.astype(np.float32), 0.5, step_size=1)
    return go.Mesh3d(
        x=vertices[:, 2],
        y=vertices[:, 1],
        z=-vertices[:, 0],
        i=faces[:, 0],
        j=faces[:, 1],
        k=faces[:, 2],
        name=name,
        color=color,
        opacity=opacity,
        flatshading=False,
        hovertemplate=f"{name}<extra></extra>",
    )


def write_interactive_viewer(sample_small: np.ndarray, ants_small: np.ndarray) -> None:
    sample_view = sample_small[::2, ::2, ::2]
    ants_view = ants_small[::2, ::2, ::2]
    figure = go.Figure(
        data=[
            mesh_trace(sample_view, "Experimental sample", "#18A7B5", 0.58),
            mesh_trace(ants_view, "Warped CCFv3 template", "#D94C92", 0.40),
        ]
    )
    figure.update_layout(
        title={"text": "Interactive 3D registration", "x": 0.02, "font": {"size": 24, "color": "#F5F7F8"}},
        paper_bgcolor="#080B0D",
        plot_bgcolor="#080B0D",
        font={"color": "#DDE8EB", "family": "Arial"},
        legend={"x": 0.02, "y": 0.94, "bgcolor": "rgba(8,11,13,0.7)"},
        margin={"l": 0, "r": 0, "t": 60, "b": 0},
        scene={
            "aspectmode": "data",
            "bgcolor": "#080B0D",
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
            "zaxis": {"visible": False},
            "camera": {"eye": {"x": 1.5, "y": 1.5, "z": 1.1}},
        },
        annotations=[
            {
                "text": "Drag to rotate | Scroll to zoom | Shift-drag to pan | Click legend entries to hide/show",
                "x": 0.5,
                "y": 0.02,
                "xref": "paper",
                "yref": "paper",
                "showarrow": False,
                "font": {"size": 15, "color": "#B7C6CC"},
            }
        ],
    )
    figure.write_html(
        OUT_INTERACTIVE,
        include_plotlyjs=True,
        full_html=True,
        config={"displaylogo": False, "responsive": True},
    )


def create_scientific_assets() -> dict[str, Path | int]:
    ASSETS.mkdir(exist_ok=True)
    SLIDES.mkdir(exist_ok=True)
    methods = {
        name: FINAL / "registration_runs" / name
        / "CB2_KP2_A1a_A.Ex_488.231.mip4.zlib_registered_CCFv3_template.tif"
        for name in ("elastix", "ants", "emlddmm")
    }

    sample = tifffile.imread(SAMPLE)
    clipped = np.minimum(sample.astype(np.float32), np.percentile(sample[sample > 0], 99.0))
    energy = clipped.sum(axis=(1, 2))
    z_index = int(np.argmax(ndimage.gaussian_filter1d(energy, 5)))
    sample_slice = sample[z_index]
    sample_small = sample[::4, ::4, ::4].astype(np.float32)
    del clipped, sample

    template = tifffile.imread(TEMPLATE)
    template_z = int(round(z_index * (template.shape[0] - 1) / 550))
    template_slice = template[template_z]
    del template

    atlas = tifffile.imread(ATLAS)
    atlas_slice = atlas[template_z]
    del atlas

    registered_slices: dict[str, np.ndarray] = {}
    ants_small = None
    for name, path in methods.items():
        volume = tifffile.imread(path)
        registered_slices[name] = volume[z_index]
        if name == "ants":
            ants_small = volume[::4, ::4, ::4].astype(np.float32)
        del volume

    sample_img = np.uint8(normalize(sample_slice) * 255)
    template_img = np.uint8(normalize(template_slice) * 255)
    atlas_img = label_rgb(atlas_slice, template_slice)
    input_triptych = ASSETS / "input_triptych.png"
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.1), dpi=170, facecolor=BG)
    panels = [sample_img, template_img, atlas_img]
    titles = ["Experimental 488 sample", "CCFv3 25 um template", "CCFv3 region labels"]
    cmaps = ["gray", "gray", None]
    for ax, panel, title, cmap in zip(axes, panels, titles, cmaps):
        ax.imshow(panel, cmap=cmap)
        ax.set_title(title, fontsize=13, fontweight="bold", pad=8)
        ax.axis("off")
    fig.tight_layout(w_pad=1.1)
    fig.savefig(input_triptych, bbox_inches="tight", facecolor=BG)
    plt.close(fig)

    ants_overlay = ASSETS / "ants_overlay.png"
    save_panel(overlay_rgb(sample_slice, registered_slices["ants"]), ants_overlay)

    comparison = ASSETS / "method_comparison.png"
    fig, axes = plt.subplots(1, 3, figsize=(13.8, 4.4), dpi=170, facecolor=BG)
    for ax, name in zip(axes, ("elastix", "ants", "emlddmm")):
        ax.imshow(overlay_rgb(sample_slice, registered_slices[name]))
        ax.set_title("emlddmm" if name == "emlddmm" else name.upper(), fontsize=14, fontweight="bold")
        ax.axis("off")
    fig.text(0.50, 0.01, "Sample = green/cyan    Warped CCF template = magenta    Agreement = pale/white", ha="center", fontsize=11)
    fig.tight_layout(rect=(0, 0.05, 1, 1), w_pad=1.1)
    fig.savefig(comparison, bbox_inches="tight", facecolor=BG)
    plt.close(fig)

    metrics = json.loads((FINAL / "evaluation_metrics.json").read_text())
    benchmark = ASSETS / "benchmark_chart.png"
    names = [row["method"] for row in metrics]
    colors = [CORAL, TEAL, GOLD]
    higher = [
        ("NCC", "template_ncc_higher_better"),
        ("Edge NCC", "template_edge_ncc_higher_better"),
        ("NMI", "template_nmi_higher_better"),
        ("Regional NCC", "subregion_weighted_mean_ncc_higher_better"),
    ]
    lower = [
        ("RMSE", "template_rmse_lower_better"),
        ("Regional RMSE", "subregion_weighted_mean_rmse_lower_better"),
    ]
    fig = plt.figure(figsize=(10.5, 5.8), dpi=170, facecolor=BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[2.2, 1], wspace=0.28)
    ax1 = fig.add_subplot(gs[0, 0])
    x = np.arange(len(higher))
    width = 0.24
    for i, (name, color) in enumerate(zip(names, colors)):
        vals = [metrics[i][key] for _, key in higher]
        ax1.bar(x + (i - 1) * width, vals, width, label=name, color=color)
    ax1.set_xticks(x, [label for label, _ in higher])
    ax1.set_ylim(0, 1.0)
    ax1.set_title("Higher is better", loc="left", fontsize=14, fontweight="bold")
    ax1.grid(axis="y", alpha=0.18)
    ax1.spines[["top", "right", "left"]].set_visible(False)
    ax1.legend(frameon=False, ncol=3, loc="upper right")

    ax2 = fig.add_subplot(gs[0, 1])
    x2 = np.arange(len(lower))
    for i, (name, color) in enumerate(zip(names, colors)):
        vals = [metrics[i][key] for _, key in lower]
        ax2.bar(x2 + (i - 1) * width, vals, width, label=name, color=color)
    ax2.set_xticks(x2, [label for label, _ in lower])
    ax2.set_ylim(0, 0.34)
    ax2.set_title("Lower is better", loc="left", fontsize=14, fontweight="bold")
    ax2.grid(axis="y", alpha=0.18)
    ax2.spines[["top", "right", "left"]].set_visible(False)
    fig.suptitle("Three-way benchmark (stride = 4)", x=0.06, y=1.02, ha="left", fontsize=18, fontweight="bold")
    fig.savefig(benchmark, bbox_inches="tight", facecolor=BG)
    plt.close(fig)

    rotation_gif = ASSETS / "registered_3d_rotation.gif"
    rotation_poster = ASSETS / "registered_3d_rotation_poster.png"
    assert ants_small is not None
    write_interactive_viewer(sample_small, ants_small)
    s = normalize(sample_small)
    a = normalize(ants_small)
    side = int(math.ceil(math.sqrt(s.shape[0] ** 2 + s.shape[2] ** 2))) + 4
    pad_z = side - s.shape[0]
    pad_x = side - s.shape[2]
    pads = ((pad_z // 2, pad_z - pad_z // 2), (0, 0), (pad_x // 2, pad_x - pad_x // 2))
    s = np.pad(s, pads)
    a = np.pad(a, pads)
    frames = []
    for angle in range(0, 360, 15):
        sr = ndimage.rotate(s, angle, axes=(0, 2), reshape=False, order=1, mode="constant")
        ar = ndimage.rotate(a, angle, axes=(0, 2), reshape=False, order=1, mode="constant")
        sm = np.max(sr, axis=1)
        am = np.max(ar, axis=1)
        frame = np.zeros((*sm.shape, 3), dtype=np.float32)
        frame[..., 0] = am
        frame[..., 1] = sm
        frame[..., 2] = 0.55 * sm + 0.65 * am
        frame = np.uint8(np.clip(frame, 0, 1) * 255)
        image = Image.fromarray(frame).resize((620, 620), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (720, 680), rgb(BLACK))
        canvas.paste(image, (50, 25))
        d = ImageDraw.Draw(canvas)
        d.ellipse((52, 638, 68, 654), fill=CYAN)
        d.text((78, 632), "sample", font=font(18), fill="#DDE8EB")
        d.ellipse((205, 638, 221, 654), fill=CORAL)
        d.text((231, 632), "warped CCF", font=font(18), fill="#DDE8EB")
        frames.append(np.asarray(canvas))
    imageio.mimsave(rotation_gif, frames, duration=0.10, loop=0)
    Image.fromarray(frames[0]).save(rotation_poster)

    return {
        "z_index": z_index,
        "input_triptych": input_triptych,
        "ants_overlay": ants_overlay,
        "comparison": comparison,
        "benchmark": benchmark,
        "rotation_gif": rotation_gif,
        "rotation_poster": rotation_poster,
        "interactive_html": OUT_INTERACTIVE,
    }


def slide_1(assets: dict[str, Path | int]) -> Image.Image:
    overlay = Image.open(assets["ants_overlay"]).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
    overlay = ImageEnhance.Brightness(overlay).enhance(0.42).filter(ImageFilter.GaussianBlur(0.8))
    base = overlay
    shade = Image.new("RGBA", (W, H), (4, 10, 12, 115))
    base = Image.alpha_composite(base.convert("RGBA"), shade).convert("RGB")
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, 28, H), fill=CYAN)
    draw.text((92, 110), "3D BRAIN REGISTRATION", font=font(20, bold=True), fill="#7ED8DF")
    draw_wrapped(draw, (92, 180), "Standardizing LSFM brains to Allen CCFv3", font(58, bold=True), WHITE, 980, 10)
    draw_wrapped(
        draw,
        (96, 345),
        "A reproducible comparison of Elastix, ANTs, and LDDMM",
        font(28),
        "#D6E1E5",
        840,
        8,
    )
    rounded(draw, (95, 600, 695, 742), "#101A1EDD", "#3B5B64")
    draw.text((125, 628), "Current result", font=font(18, bold=True), fill="#7ED8DF")
    draw.text((125, 670), "ANTs leads the quantitative benchmark", font=font(27, bold=True), fill=WHITE)
    draw.text((96, 825), "Project update | June 2026", font=font(18), fill="#CBD7DB")
    return base


def slide_2(assets: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "Why standardize a 3D brain?", "Problem and objective", 2)
    contain_image(base, Image.open(assets["input_triptych"]), (70, 180, 1530, 650))
    rounded(draw, (90, 684, 1510, 820), WHITE, "#D8E0E3")
    draw.text((125, 710), "Goal", font=font(21, bold=True), fill=TEAL)
    draw_wrapped(
        draw,
        (225, 706),
        "Transform the CCF template and its anatomical labels into each experimental sample space so signals can be compared by standardized region.",
        font(24),
        INK,
        1220,
        7,
    )
    footer(draw, "Fixed image: experimental 488 channel | Moving images: CCFv3 template + annotation atlas")
    return base


def slide_3(_: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "One pipeline, three registration engines", "Standardized workflow", 3)
    nodes = [
        ("Inputs", "Sample\nTemplate\nAtlas labels", CORAL),
        ("Validated I/O", "TIFF -> NIfTI/VTK\nShape + voxel checks", CYAN),
        ("Registration", "Elastix\nANTs\nemlddmm", TEAL),
        ("Transform labels", "Nearest-neighbor\ninterpolation", GOLD),
        ("Evaluation", "Global metrics\nSubregion metrics\nVisual QC", GREEN),
    ]
    x_positions = [70, 370, 670, 970, 1270]
    for i, ((title, body, color), x) in enumerate(zip(nodes, x_positions)):
        rounded(draw, (x, 250, x + 245, 520), WHITE, "#D3DCDF")
        draw.rectangle((x, 250, x + 245, 266), fill=color)
        draw.text((x + 24, 294), title, font=font(23, bold=True), fill=INK)
        y = 350
        for line in body.split("\n"):
            draw.text((x + 24, y), line, font=font(20), fill=MUTED)
            y += 38
        if i < len(nodes) - 1:
            draw.line((x + 250, 385, x_positions[i + 1] - 8, 385), fill="#84969D", width=4)
            draw.polygon(
                [(x_positions[i + 1] - 18, 374), (x_positions[i + 1] - 4, 385), (x_positions[i + 1] - 18, 396)],
                fill="#84969D",
            )
    rounded(draw, (185, 610, 1415, 760), "#E6F2F3")
    draw.text((230, 638), "Engineering principle", font=font(20, bold=True), fill=TEAL)
    draw_wrapped(
        draw,
        (230, 683),
        "Run heavy intermediates on the external SSD, reject empty or non-finite volumes, then copy only validated outputs to the NAS.",
        font(25, bold=True),
        INK,
        1110,
        8,
    )
    footer(draw, "All methods use the same fixed image, moving template, atlas, and evaluation code")
    return base


def slide_4(_: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "The three registration approaches", "Methods", 4)
    cards = [
        ("Elastix", "Affine + B-spline", CORAL, ["Mature intensity-based toolkit", "Flexible parameter files", "Strong baseline; practical workflow"]),
        ("ANTs", "Rigid + affine + SyN", TEAL, ["Diffeomorphic nonlinear transform", "Mutual-information objective", "Best current global and regional scores"]),
        ("emlddmm", "Affine + velocity-field LDDMM", GOLD, ["Large-deformation diffeomorphism", "Explicit velocity/displacement fields", "Valid output; weaker intensity agreement"]),
    ]
    for i, (name, subtitle, color, bullets) in enumerate(cards):
        x0 = 70 + i * 500
        rounded(draw, (x0, 195, x0 + 455, 755), WHITE, "#D7DFE2")
        draw.rectangle((x0, 195, x0 + 455, 218), fill=color)
        draw.text((x0 + 34, 255), name, font=font(34, bold=True), fill=INK)
        draw.text((x0 + 34, 310), subtitle, font=font(21, bold=True), fill=color)
        y = 390
        for bullet in bullets:
            draw.ellipse((x0 + 36, y + 7, x0 + 48, y + 19), fill=color)
            y = draw_wrapped(draw, (x0 + 65, y), bullet, font(21), MUTED, 340, 7) + 30
        label = "BASELINE" if i == 0 else "CURRENT LEAD" if i == 1 else "EXPERIMENTAL"
        rounded(draw, (x0 + 34, 680, x0 + 250, 720), "#EEF2F3")
        draw.text((x0 + 50, 691), label, font=font(15, bold=True), fill=color)
    footer(draw, "References: Klein et al. 2010 (elastix); Avants et al. 2011 (ANTs); Beg et al. 2005 (LDDMM)")
    return base


def slide_5(assets: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "Main method: ANTs SyN registration", "Method detail", 5)
    contain_image(base, Image.open(assets["ants_overlay"]), (820, 185, 1530, 745), fill=BLACK)
    draw.text((845, 710), "Sample = green | warped CCF = magenta", font=font(17), fill=WHITE)
    steps = [
        ("1", "Rigid alignment", "Correct global pose and center."),
        ("2", "Affine alignment", "Estimate rotation, scale, shear, and translation."),
        ("3", "SyN deformation", "Optimize a smooth invertible nonlinear field."),
        ("4", "Label propagation", "Apply the same transform with nearest-neighbor interpolation."),
    ]
    y = 205
    for number, title, body in steps:
        draw.ellipse((85, y, 137, y + 52), fill=TEAL)
        draw.text((111, y + 25), number, font=font(22, bold=True), fill=WHITE, anchor="mm")
        draw.text((165, y - 2), title, font=font(25, bold=True), fill=INK)
        draw_wrapped(draw, (165, y + 37), body, font(20), MUTED, 610, 6)
        y += 140
    rounded(draw, (85, 750, 775, 820), "#E4F1F2")
    draw.text((115, 770), "Output space: experimental sample coordinates", font=font(22, bold=True), fill=TEAL)
    footer(draw, "The experimental sample is fixed; the CCF anatomy and labels move into sample space")
    return base


def slide_6(assets: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "Benchmark design and current result", "Evaluation", 6)
    contain_image(base, Image.open(assets["benchmark"]), (70, 170, 1120, 760))
    rounded(draw, (1155, 190, 1525, 520), WHITE, "#D6DFE2")
    draw.text((1190, 225), "Metrics", font=font(24, bold=True), fill=INK)
    metric_lines = ["NCC / edge NCC", "Mutual information / NMI", "RMSE / MAE", "Weighted subregion scores"]
    y = 285
    for line in metric_lines:
        draw.ellipse((1192, y + 7, 1204, y + 19), fill=CYAN)
        draw.text((1220, y), line, font=font(19), fill=MUTED)
        y += 52
    rounded(draw, (1155, 555, 1525, 755), "#E4F1F2")
    draw.text((1190, 590), "Current read", font=font(20, bold=True), fill=TEAL)
    draw.text((1190, 630), "ANTs leads", font=font(31, bold=True), fill=INK)
    draw.text((1190, 675), "NCC 0.8558", font=font(22, bold=True), fill=TEAL)
    draw.text((1190, 710), "Regional NCC 0.3765", font=font(19), fill=MUTED)
    footer(draw, "Proxy metrics support comparison but do not replace manual landmarks or expert anatomical review")
    return base


def slide_7(assets: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "Qualitative comparison in sample space", "Visual QC", 7)
    contain_image(base, Image.open(assets["comparison"]), (70, 170, 1530, 690))
    rounded(draw, (120, 720, 1480, 815), WHITE, "#D7DFE2")
    draw.text((155, 746), "Interpretation", font=font(19, bold=True), fill=TEAL)
    draw_wrapped(
        draw,
        (305, 740),
        "ANTs shows the strongest quantitative agreement. Remaining mismatches must be checked at anatomical boundaries and across the full z-stack.",
        font(22),
        INK,
        1100,
        6,
    )
    footer(draw, f"Representative coronal slice z = {assets['z_index']} | Same sample and display scaling for all methods")
    return base


def slide_8(assets: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BLACK))
    draw = header(base, "Explore the registered brain in 3D", "Interactive result", 8, dark=True)
    contain_image(base, Image.open(assets["rotation_poster"]), (800, 150, 1530, 790), fill=BLACK)
    rounded(draw, (75, 190, 700, 365), "#101A1E", "#3B5B64")
    draw.text((110, 220), "Mouse controls", font=font(22, bold=True), fill="#7ED8DF")
    draw.text((110, 268), "Drag: rotate", font=font(24, bold=True), fill=WHITE)
    draw.text((310, 268), "Scroll: zoom", font=font(24, bold=True), fill=WHITE)
    draw.text((110, 315), "Shift-drag: pan", font=font(22), fill="#C7D4D9")

    rounded(draw, (75, 405, 700, 590), "#102326", "#1D6970")
    draw.text((110, 438), "CLICK THE BRAIN", font=font(21, bold=True), fill="#7ED8DF")
    draw.text((110, 478), "Open interactive WebGL viewer", font=font(28, bold=True), fill=WHITE)
    draw_wrapped(draw, (110, 525), "Start the local viewer before presenting; the link opens in a browser.", font(20), "#C7D4D9", 540, 6)

    draw.text((75, 645), "Google Slides limitation", font=font(18, bold=True), fill="#F0C66A")
    draw_wrapped(
        draw,
        (75, 682),
        "Slides cannot embed a live 3D canvas. A linked browser viewer is the reliable option; host the HTML for use on another computer.",
        font(21),
        "#C7D4D9",
        630,
        7,
    )
    footer(draw, "Interactive surface: sample = cyan, warped CCF = magenta | Link target: http://localhost:8000", dark=True)
    return base


def slide_9_speed(_: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "How the pipeline controls time and memory", "Implementation logic", 9)

    rounded(draw, (70, 180, 760, 555), WHITE, "#D7DFE2")
    draw.text((105, 215), "Coarse-to-fine optimization", font=font(27, bold=True), fill=INK)
    draw.text((105, 260), "Solve large structures first, then refine detail.", font=font(21), fill=MUTED)
    pyramids = [
        ("ANTs rigid + affine", ["12x", "8x", "4x", "2x"], CORAL),
        ("ANTs SyN", ["10x", "6x", "4x", "2x", "1x"], TEAL),
        ("emlddmm", ["16x", "8x"], GOLD),
    ]
    y = 325
    for label, levels, color in pyramids:
        draw.text((105, y + 6), label, font=font(19, bold=True), fill=INK)
        x = 330
        for j, level in enumerate(levels):
            size = 48 - min(j, 3) * 5
            draw.rounded_rectangle((x, y, x + 62, y + size), radius=6, fill=color)
            draw.text((x + 31, y + size // 2), level, font=font(16, bold=True), fill=WHITE, anchor="mm")
            x += 76
        y += 72

    rounded(draw, (800, 180, 1530, 555), "#E5F1F2", "#BFDADD")
    draw.text((840, 215), "Preserve the experimental sample", font=font(27, bold=True), fill=TEAL)
    strategy = [
        "Sample is the fixed reference.",
        "CCFv3 template moves into sample space.",
        "Original sample voxels are never resampled.",
        "Atlas labels use nearest-neighbor interpolation.",
        "Eight CPU threads and SSD scratch reduce I/O delay.",
    ]
    y = 285
    for point in strategy:
        draw.ellipse((842, y + 7, 854, y + 19), fill=TEAL)
        y = draw_wrapped(draw, (875, y), point, font(21), INK, 590, 6) + 20

    rounded(draw, (120, 615, 1480, 805), "#FFF6E3", "#E5CB91")
    draw.text((155, 646), "Chunking: current truth", font=font(23, bold=True), fill="#9A6810")
    draw_wrapped(
        draw,
        (155, 692),
        "The current wrappers do not manually split and restitch spatial chunks. ANTs/ITK parallelizes internal image regions. If explicit tiling is added, each chunk needs an overlap halo, independent resampling, halo cropping, and blended stitching to prevent seams.",
        font(22),
        INK,
        1250,
        7,
    )
    footer(draw, "Actual settings: ANTs shrink factors 12x8x4x2 and 10x6x4x2x1; emlddmm downI/downJ 16 then 8")
    return base


def slide_10(_: dict[str, Path | int]) -> Image.Image:
    base = Image.new("RGB", (W, H), rgb(BG))
    draw = header(base, "Take-home message and next steps", "Conclusion", 10)
    rounded(draw, (70, 180, 950, 750), WHITE, "#D7DFE2")
    draw.text((110, 225), "What is working", font=font(27, bold=True), fill=TEAL)
    points = [
        "A reproducible three-method registration pipeline",
        "Validated TIFF/NIfTI/VTK conversion and fail-fast checks",
        "Per-region outputs in experimental sample space",
        "SSD scratch workflow that avoids live NAS congestion",
    ]
    y = 300
    for point in points:
        draw.ellipse((112, y + 8, 126, y + 22), fill=TEAL)
        y = draw_wrapped(draw, (145, y), point, font(23), INK, 720, 7) + 30

    rounded(draw, (990, 180, 1530, 750), "#E8EFF1")
    draw.text((1030, 225), "Next", font=font(27, bold=True), fill=CORAL)
    next_points = [
        "Add manual landmarks or expert segmentations",
        "Review low-scoring subregions in Fiji",
        "Test a structural/autofluorescence channel",
        "Run mBrainAligner in a compatible Linux environment",
    ]
    y = 300
    for point in next_points:
        draw.ellipse((1032, y + 8, 1046, y + 22), fill=CORAL)
        y = draw_wrapped(draw, (1065, y), point, font(22), INK, 390, 7) + 28
    rounded(draw, (240, 780, 1360, 833), TEAL)
    draw.text((800, 806), "Current quantitative lead: ANTs", font=font(24, bold=True), fill=WHITE, anchor="mm")
    footer(draw, "Final outputs: /Volumes/Wenxi/Proj_reg_brain/ssd_20260628_153426")
    return base


def make_slides(assets: dict[str, Path | int]) -> list[Path]:
    builders = [slide_1, slide_2, slide_3, slide_4, slide_5, slide_6, slide_7, slide_8, slide_9_speed, slide_10]
    paths = []
    for i, builder in enumerate(builders, start=1):
        image = builder(assets)
        path = SLIDES / f"slide_{i:02d}.png"
        image.save(path, optimize=True)
        paths.append(path)
    return paths


def picture_xml(
    name: str,
    rid: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    pic_id: int,
    hyperlink_rid: str | None = None,
) -> str:
    if hyperlink_rid:
        nonvisual = f'<p:cNvPr id="{pic_id}" name="{escape(name)}"><a:hlinkClick r:id="{hyperlink_rid}"/></p:cNvPr>'
    else:
        nonvisual = f'<p:cNvPr id="{pic_id}" name="{escape(name)}"/>'
    return f"""<p:pic>
      <p:nvPicPr>{nonvisual}<p:cNvPicPr/><p:nvPr/></p:nvPicPr>
      <p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
      <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
    </p:pic>"""


def slide_xml(extra_picture: bool = False) -> str:
    pictures = picture_xml("Slide artwork", "rId2", 0, 0, EMU_W, EMU_H, 2)
    if extra_picture:
        x = int(6.83 * 914400)
        y = int(1.36 * 914400)
        cx = int(5.93 * 914400)
        cy = int(5.27 * 914400)
        pictures += picture_xml("Open interactive 3D registration", "rId3", x, y, cx, cy, 3, "rId4")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:cSld><p:spTree>
  <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
  <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
  {pictures}
 </p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def build_pptx(slide_paths: list[Path], poster_path: Path) -> None:
    temp = ASSETS / "pptx_package"
    if temp.exists():
        shutil.rmtree(temp)
    for folder in [
        "_rels",
        "docProps",
        "ppt/_rels",
        "ppt/theme",
        "ppt/slideMasters/_rels",
        "ppt/slideLayouts/_rels",
        "ppt/slides/_rels",
        "ppt/media",
    ]:
        (temp / folder).mkdir(parents=True, exist_ok=True)

    (temp / "[Content_Types].xml").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gif" ContentType="image/gif"/>
 <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
 <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
 <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
 <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
 <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
 <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
"""
        + "\n".join(
            f' <Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(1, len(slide_paths) + 1)
        )
        + "\n</Types>",
        encoding="utf-8",
    )
    (temp / "_rels/.rels").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
 <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (temp / "docProps/core.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <dc:title>3D Brain Registration Pipeline</dc:title><dc:creator>Wenxi Li</dc:creator>
 <cp:lastModifiedBy>Codex</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
 <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>""",
        encoding="utf-8",
    )
    (temp / "docProps/app.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
 <Application>Microsoft Office PowerPoint</Application><PresentationFormat>Widescreen</PresentationFormat>
 <Slides>{len(slide_paths)}</Slides><Notes>0</Notes><Company></Company><AppVersion>16.0000</AppVersion>
</Properties>""",
        encoding="utf-8",
    )

    slide_ids = "\n".join(f'  <p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, len(slide_paths) + 1))
    (temp / "ppt/presentation.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
 <p:sldIdLst>{slide_ids}</p:sldIdLst>
 <p:sldSz cx="{EMU_W}" cy="{EMU_H}" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/>
 <p:defaultTextStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:defaultTextStyle>
</p:presentation>""",
        encoding="utf-8",
    )
    presentation_rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    ]
    presentation_rels.extend(
        f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, len(slide_paths) + 1)
    )
    (temp / "ppt/_rels/presentation.xml.rels").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n '
        + "\n ".join(presentation_rels)
        + "\n</Relationships>",
        encoding="utf-8",
    )

    (temp / "ppt/slideMasters/slideMaster1.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
 <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
 </p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>
 <p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst>
 <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>""",
        encoding="utf-8",
    )
    (temp / "ppt/slideMasters/_rels/slideMaster1.xml.rels").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>""",
        encoding="utf-8",
    )
    (temp / "ppt/slideLayouts/slideLayout1.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
 <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
 <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
 </p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>""",
        encoding="utf-8",
    )
    (temp / "ppt/slideLayouts/_rels/slideLayout1.xml.rels").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>""",
        encoding="utf-8",
    )
    (temp / "ppt/theme/theme1.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Scientific">
 <a:themeElements><a:clrScheme name="Scientific"><a:dk1><a:srgbClr val="172126"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
 <a:dk2><a:srgbClr val="314148"/></a:dk2><a:lt2><a:srgbClr val="F5F7F8"/></a:lt2><a:accent1><a:srgbClr val="007C83"/></a:accent1>
 <a:accent2><a:srgbClr val="D45B4E"/></a:accent2><a:accent3><a:srgbClr val="D7A43B"/></a:accent3><a:accent4><a:srgbClr val="2D8A5F"/></a:accent4>
 <a:accent5><a:srgbClr val="18A7B5"/></a:accent5><a:accent6><a:srgbClr val="74858C"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>
 <a:fontScheme name="Arial"><a:majorFont><a:latin typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/></a:minorFont></a:fontScheme>
 <a:fmtScheme name="Scientific"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
 </a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/>
</a:theme>""",
        encoding="utf-8",
    )

    interactive_media = "image_3d_interactive.png"
    shutil.copy2(poster_path, temp / "ppt/media" / interactive_media)
    for i, path in enumerate(slide_paths, start=1):
        media_name = f"slide_{i:02d}.png"
        shutil.copy2(path, temp / "ppt/media" / media_name)
        extra = i == 8
        (temp / f"ppt/slides/slide{i}.xml").write_text(slide_xml(extra), encoding="utf-8")
        rels = [
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>',
            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/{media_name}"/>',
        ]
        if extra:
            rels.append(
                f'<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/{interactive_media}"/>'
            )
            rels.append(
                '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="http://localhost:8000/Brain_Registration_Interactive_3D.html" TargetMode="External"/>'
            )
        (temp / f"ppt/slides/_rels/slide{i}.xml.rels").write_text(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n '
            + "\n ".join(rels)
            + "\n</Relationships>",
            encoding="utf-8",
        )

    with zipfile.ZipFile(OUT_PPTX, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(temp.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(temp).as_posix())


def write_notes() -> None:
    notes = """# Brain Registration Project: Speaker Notes

Target length: 7-9 minutes, roughly 35-50 seconds per slide.

## Slide 1 - Project framing
I am building a reproducible pipeline that maps 3D LSFM brain volumes into a common Allen CCFv3 anatomical framework. The current comparison includes Elastix, ANTs, and emlddmm/LDDMM. The goal is not only to produce a visually plausible registration, but to standardize conversion, transformation, validation, and evaluation.

## Slide 2 - Why registration matters
The experimental 488 stack and the CCF template have different shapes and coordinate systems. The annotation atlas supplies anatomical region IDs. By warping the template and labels into sample space, experimental signal can be summarized consistently by brain region while preserving the original sample volume.

## Slide 3 - Shared pipeline
All methods receive exactly the same fixed sample, template, and atlas. Validated conversion is critical: an earlier c3d conversion reduced the sample from 35.6 million nonzero voxels to only 182. The new pipeline verifies shape, values, finiteness, and nonzero content. Heavy I/O is performed on the external SSD, then validated results are copied once to the NAS.

## Slide 4 - Three methods
Elastix provides an affine plus B-spline baseline and is highly configurable. ANTs combines rigid, affine, and SyN diffeomorphic registration and currently performs best. emlddmm implements a large-deformation velocity-field model and provides explicit deformation products, but its current intensity agreement is lower.

## Slide 5 - Main method
ANTs is the main method at this stage. It first corrects pose, then estimates affine differences, and finally computes a smooth nonlinear SyN deformation. The same composite transform is applied to the annotation atlas with nearest-neighbor interpolation so region IDs are not blended.

## Slide 6 - Benchmark
The benchmark uses the same downsampling stride for all methods. NCC, edge NCC, mutual information, and NMI are higher-is-better. RMSE and MAE are lower-is-better. Per-subregion metrics evaluate each warped label independently. ANTs leads global NCC at 0.8558 and weighted regional NCC at 0.3765.

## Slide 7 - Visual QC
These overlays show the sample in green/cyan and the warped CCF template in magenta. Pale or white structures indicate overlap. The same slice and scaling are used across methods. Quantitative ranking is useful, but boundaries and the full z-stack still require expert visual inspection.

## Slide 8 - Interactive 3D result
Click the brain image to open the browser-based WebGL viewer. Drag to rotate, scroll to zoom, and shift-drag to pan. Before presenting locally, run `python -m http.server 8000` from the project folder. Google Slides cannot contain a live 3D canvas, so the HTML must be hosted at a public URL when presenting from another computer.

## Slide 9 - Implementation and speed
The wrappers use multiresolution pyramids rather than optimizing every parameter at full resolution from the beginning. ANTs uses shrink factors 12, 8, 4, and 2 for rigid/affine stages and 10, 6, 4, 2, and 1 for SyN. emlddmm uses 16-fold then 8-fold downsampling. The experimental sample remains the fixed reference and is never resampled in the forward result; the smaller CCF template and labels move into sample space. The current wrappers do not explicitly split and restitch chunks. A tiled implementation would require overlap halos and blended stitching to prevent seams.

## Slide 10 - Conclusion
The project now has a validated, reproducible three-method benchmark and a storage-safe execution pattern. ANTs is the current quantitative lead. The next scientific step is to add manual landmarks or expert reference segmentations, inspect low-scoring regions, and test whether a structural or autofluorescence channel improves registration.
"""
    OUT_NOTES.write_text(notes, encoding="utf-8")


def make_preview(slide_paths: list[Path]) -> None:
    thumb_w, thumb_h = 480, 270
    rows = math.ceil(len(slide_paths) / 3)
    canvas = Image.new("RGB", (thumb_w * 3, thumb_h * rows), "#D8DEE1")
    for i, path in enumerate(slide_paths):
        image = Image.open(path).convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        canvas.paste(image, ((i % 3) * thumb_w, (i // 3) * thumb_h))
    canvas.save(OUT_PREVIEW)


def main() -> None:
    if not FINAL.exists():
        raise SystemExit(f"Missing validated run: {FINAL}")
    assets = create_scientific_assets()
    slides = make_slides(assets)
    build_pptx(slides, Path(assets["rotation_poster"]))
    write_notes()
    make_preview(slides)
    print(f"Wrote {OUT_PPTX}")
    print(f"Wrote {OUT_NOTES}")
    print(f"Wrote {OUT_PREVIEW}")


if __name__ == "__main__":
    main()
