"""
ขั้นที่ 4: ดึงค่า NDVI 12 เดือนของทุกจุดใน label_points แล้วทำกราฟช่วยตัดสินใจ label

ใช้: python 04_point_curves.py
ต้องมี: data/label_points.gpkg และ output/ndvi_stack_12m.tif

ผลลัพธ์:
- output/label_points_ndvi.csv          ตาราง fid, อำเภอ, crop, note + ค่า 12 เดือน
- output/curves/class_mean.png          กราฟเฉลี่ยของแต่ละคลาสที่ label แล้ว (ไว้เป็นเกณฑ์)
- output/curves/unsure_XX.png           กราฟของจุด unsure / ยังไม่ label หน้าละ 16 จุด
  (มีเส้นเฉลี่ยของคลาสที่ label แล้ววาดจาง ๆ ไว้เทียบในทุกช่อง)

รันซ้ำได้ทุกครั้งที่ label เพิ่ม - กราฟเฉลี่ยของคลาสจะแม่นขึ้นเรื่อย ๆ
"""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

BASE = Path(__file__).resolve().parent
POINTS = BASE / "data" / "label_points.gpkg"
STACK = BASE / "output" / "ndvi_stack_12m.tif"
OUT = BASE / "output"
CURVES = OUT / "curves"

# ฟอนต์ที่มีภาษาไทยบน Windows (ถ้าไม่มีจะใช้ฟอนต์ถัดไป)
plt.rcParams["font.family"] = ["Tahoma", "Leelawadee UI", "DejaVu Sans"]

CLASS_COLORS = {"rice": "#1f77b4", "sugarcane": "#2ca02c", "cassava": "#ff7f0e",
                "maize": "#bcbd22", "forest": "#17becf", "water": "#7f7f7f",
                "urban": "#8c564b", "other": "#9467bd", "orchard": "#e377c2"}


def main():
    CURVES.mkdir(parents=True, exist_ok=True)
    pts = gpd.read_file(POINTS, fid_as_index=True)  # ให้เลข fid ตรงกับใน QGIS
    print(f"อ่าน {len(pts)} จุดจาก {POINTS.name}")

    with rasterio.open(STACK) as src:
        months = [d or f"band{i+1}" for i, d in enumerate(src.descriptions)]
        pts_r = pts.to_crs(src.crs)
        coords = [(g.x, g.y) for g in pts_r.geometry]
        vals = np.array([v for v in src.sample(coords)], dtype="float32")
        if src.nodata is not None and not np.isnan(src.nodata):
            vals[vals == src.nodata] = np.nan

    df = pd.DataFrame(vals, columns=months)
    df.insert(0, "fid", pts.index.values)
    df.insert(1, "amphoe", pts.get("adm2_name1", pd.Series([""] * len(pts))).values)
    df.insert(2, "crop", pts.get("crop", pd.Series([None] * len(pts))).fillna("").str.strip().values)
    df.insert(3, "note", pts.get("note", pd.Series([None] * len(pts))).fillna("").values)
    df.to_csv(OUT / "label_points_ndvi.csv", index=False, encoding="utf-8-sig")
    print(f"บันทึก {OUT / 'label_points_ndvi.csv'}")

    x = np.arange(len(months))
    short = [m[2:] if len(m) == 7 else m for m in months]  # 2025-05 -> 25-05

    # 1) กราฟเฉลี่ยของคลาสที่ label แล้ว
    labeled = df[df.crop.isin(CLASS_COLORS)]
    means = {}
    fig, ax = plt.subplots(figsize=(11, 6))
    for cls, g in labeled.groupby("crop"):
        m = g[months].mean().values
        sd = g[months].std().values
        means[cls] = m
        c = CLASS_COLORS[cls]
        ax.plot(x, m, "-o", color=c, label=f"{cls} (n={len(g)})")
        if len(g) > 1:
            ax.fill_between(x, m - sd, m + sd, color=c, alpha=0.12)
    ax.set_xticks(x, short, rotation=45)
    ax.set_ylim(-0.3, 1.0)
    ax.set_ylabel("NDVI")
    ax.set_title("ค่าเฉลี่ย NDVI ของแต่ละคลาส (แถบจาง = ±1 SD)")
    ax.grid(alpha=0.3)
    if means:
        ax.legend(loc="lower left", fontsize=9)
    fig.savefig(CURVES / "class_mean.png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"คลาสที่ label แล้ว: {labeled.crop.value_counts().to_dict()}")

    # 2) กราฟของจุดที่ยังไม่ได้ตัดสิน
    todo = df[~df.crop.isin(CLASS_COLORS)].reset_index(drop=True)
    print(f"จุดที่ยังต้องตัดสิน (unsure/ว่าง): {len(todo)}")
    for old in CURVES.glob("unsure_*.png"):
        old.unlink()
    per_page = 16
    for p in range(int(np.ceil(len(todo) / per_page))):
        chunk = todo.iloc[p * per_page:(p + 1) * per_page]
        fig, axes = plt.subplots(4, 4, figsize=(16, 13), sharex=True, sharey=True)
        for ax in axes.ravel():
            ax.axis("off")
        for ax, (_, r) in zip(axes.ravel(), chunk.iterrows()):
            ax.axis("on")
            for cls, m in means.items():
                ax.plot(x, m, color=CLASS_COLORS[cls], alpha=0.35, lw=1)
            ax.plot(x, r[months].values.astype(float), "-o", color="black", lw=2, ms=3)
            ax.set_title(f"fid {r.fid} · {r.amphoe}", fontsize=10)
            ax.set_ylim(-0.3, 1.0)
            ax.grid(alpha=0.3)
            ax.set_xticks(x, short, rotation=90, fontsize=7)
        fig.suptitle("เส้นดำ = จุดนี้ · เส้นสีจาง = ค่าเฉลี่ยคลาสที่ label แล้ว", fontsize=12)
        fig.savefig(CURVES / f"unsure_{p + 1:02d}.png", dpi=110, bbox_inches="tight")
        plt.close(fig)
    print(f"บันทึกกราฟไว้ที่ {CURVES}")


if __name__ == "__main__":
    main()
