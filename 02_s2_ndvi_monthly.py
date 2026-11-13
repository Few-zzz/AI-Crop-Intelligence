"""
ขั้นที่ 2.2: NDVI รายเดือน 12 เดือน (พ.ค. 2025 - เม.ย. 2026) ของพื้นที่ AOI

ใช้: python 02_s2_ndvi_monthly.py
ผลลัพธ์: output/monthly/ndvi_YYYY-MM.tif (เดือนละไฟล์) + output/ndvi_monthly_overview.png

- รันซ้ำได้: เดือนที่ทำเสร็จแล้วจะข้าม (ถ้าเน็ตหลุดกลางทาง รันใหม่ได้เลย)
- หน้าฝนเมฆเยอะ จึงยอมรับภาพเมฆสูงถึง 70% แล้วใช้ SCL ตัดเมฆรายพิกเซลแทน
"""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from odc.stac import load
from pystac_client import Client
from rasterio.features import geometry_mask

# ---------------- ตั้งค่า ----------------
BASE = Path(__file__).resolve().parent
AOI_FILE = BASE / "data" / "aoi.geojson"
OUT_DIR = BASE / "output" / "monthly"

START_MONTH = "2025-05"
N_MONTHS = 12
MAX_CLOUD = 70
RESOLUTION = 20
CRS = "EPSG:32647"

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
VALID_SCL = [4, 5, 6, 7]
# ตรวจกับค่าดิบจริงแล้ว (00_check_offset.py): พืชมีค่าดิบแดง p50 = 579 -> ไฟล์ "ไม่ได้" บวก +1000 ไว้
# แม้ metadata จะเขียนว่า offset -0.1 ก็ตาม จึงใช้ OFFSET = 0
SCALE, OFFSET = 0.0001, 0.0


def month_ndvi(catalog, aoi, outside_mask_cache, month: pd.Period):
    date_range = f"{month.start_time:%Y-%m-%d}/{month.end_time:%Y-%m-%d}"
    bbox = [float(v) for v in aoi.total_bounds]

    items = list(catalog.search(
        collections=[COLLECTION], bbox=bbox, datetime=date_range,
        query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
    ).items())
    print(f"[{month}] เจอ {len(items)} ภาพ", flush=True)
    if not items:
        return None, None

    ds = load(items, bands=["red", "nir", "scl"], bbox=bbox, crs=CRS,
              resolution=RESOLUTION, groupby="solar_day",
              chunks={"x": 2048, "y": 2048})

    valid = ds.scl.isin(VALID_SCL)
    red = ds.red * SCALE + OFFSET
    nir = ds.nir * SCALE + OFFSET
    # ค่าดิบ 0 = ไม่มีข้อมูล จึงตัดพิกเซลที่ค่า <= 0 ทิ้ง (กันหารด้วย 0 และค่าเพี้ยน)
    ok = valid & (red > 0) & (nir > 0)
    ndvi = ((nir - red) / (nir + red)).where(ok).median(dim="time").compute()

    geobox = ndvi.odc.geobox
    key = (tuple(geobox.shape), tuple(geobox.affine))
    if key not in outside_mask_cache:
        outside_mask_cache[key] = geometry_mask(
            aoi.to_crs(CRS).geometry, out_shape=tuple(geobox.shape),
            transform=geobox.affine)
    arr = np.where(outside_mask_cache[key], np.nan, ndvi.values).astype("float32")
    return arr, geobox


def save_tif(path, arr, geobox):
    with rasterio.open(path, "w", driver="GTiff",
                       height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype="float32", crs=CRS, transform=geobox.affine,
                       nodata=np.nan, compress="deflate") as dst:
        dst.write(arr, 1)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    aoi = gpd.read_file(AOI_FILE).to_crs("EPSG:4326")
    catalog = Client.open(STAC_URL)
    months = pd.period_range(START_MONTH, periods=N_MONTHS, freq="M")
    mask_cache = {}
    summary = []

    for m in months:
        tif = OUT_DIR / f"ndvi_{m}.tif"
        if tif.exists():
            print(f"[{m}] มีไฟล์แล้ว ข้าม")
        else:
            arr, geobox = month_ndvi(catalog, aoi, mask_cache, m)
            if arr is None:
                print(f"[{m}] ไม่มีภาพ ข้ามเดือนนี้")
                continue
            save_tif(tif, arr, geobox)

        with rasterio.open(tif) as src:
            a = src.read(1)
        cover = np.isfinite(a).sum()
        summary.append((str(m), cover, float(np.nanmean(a)) if cover else np.nan))
        print(f"[{m}] พิกเซลใช้ได้ {cover:,}  NDVI เฉลี่ย {summary[-1][2]:.3f}")

    # สรุป: % พิกเซลที่ใช้ได้เทียบกับเดือนที่ดีที่สุด (บอกว่าเดือนไหนเมฆบังมาก)
    df = pd.DataFrame(summary, columns=["month", "valid_px", "ndvi_mean"])
    if not df.empty:
        df["valid_pct_vs_best"] = (df.valid_px / df.valid_px.max() * 100).round(1)
        df.to_csv(BASE / "output" / "ndvi_monthly_summary.csv", index=False)
        print("\n", df.to_string(index=False))

    # ภาพรวม 12 เดือนในหน้าเดียว
    tifs = sorted(OUT_DIR.glob("ndvi_*.tif"))
    if tifs:
        cols = 4
        rows = int(np.ceil(len(tifs) / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 4, rows * 4))
        for ax in np.ravel(axes):
            ax.axis("off")
        for ax, t in zip(np.ravel(axes), tifs):
            with rasterio.open(t) as src:
                a = src.read(1, out_shape=(src.height // 4, src.width // 4))
            im = ax.imshow(a, cmap="RdYlGn", vmin=-0.2, vmax=0.9)
            ax.set_title(t.stem.replace("ndvi_", ""))
        fig.colorbar(im, ax=axes, shrink=0.6, label="NDVI")
        out_png = BASE / "output" / "ndvi_monthly_overview.png"
        fig.savefig(out_png, dpi=120, bbox_inches="tight")
        print(f"\nบันทึกภาพรวม {out_png}")


if __name__ == "__main__":
    main()
