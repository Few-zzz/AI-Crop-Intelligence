"""
ขั้นที่ 2: ค้นหาภาพ Sentinel-2 ของพื้นที่ AOI แล้วคำนวณ NDVI (ภาพรวมรายเดือนแบบ median)

ใช้: python 01_s2_ndvi.py
ต้องมีไฟล์ data/aoi.geojson อยู่ในโฟลเดอร์เดียวกับสคริปต์นี้

แหล่งภาพ: Earth Search (Element 84) - Sentinel-2 L2A บน AWS Open Data
ฟรี ไม่ต้องสมัครบัญชี เป็นภาพชุดเดียวกับ Copernicus
"""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from odc.stac import load
from pystac_client import Client
from rasterio.features import geometry_mask

# ---------------- ตั้งค่า ----------------
BASE = Path(__file__).resolve().parent
AOI_FILE = BASE / "data" / "aoi.geojson"
OUT_DIR = BASE / "output"

DATE_RANGE = "2026-01-01/2026-01-31"  # หน้าแล้ง เมฆน้อย เหมาะกับการทดสอบรอบแรก
MAX_CLOUD = 20                         # % เมฆสูงสุดของทั้งภาพ
RESOLUTION = 20                        # เมตร (รอบแรกใช้ 20 ม. ให้เร็ว ค่อยเปลี่ยนเป็น 10 ทีหลัง)
CRS = "EPSG:32647"                     # UTM zone 47N

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# คลาสใน SCL (Scene Classification) ที่ถือว่าเป็นพิกเซลใช้ได้
# 4 = พืช, 5 = ดินเปล่า, 6 = น้ำ, 7 = ไม่ระบุคลาส
# ที่ตัดทิ้ง: 0 nodata, 1 saturated, 3 เงาเมฆ, 8-10 เมฆ/เมฆบาง
VALID_SCL = [4, 5, 6, 7]


def main():
    OUT_DIR.mkdir(exist_ok=True)

    # 1) อ่าน AOI
    aoi = gpd.read_file(AOI_FILE).to_crs("EPSG:4326")
    print(f"AOI: {len(aoi)} อำเภอ, bbox = {[round(float(v), 3) for v in aoi.total_bounds]}")

    # 2) ค้นหาภาพ
    catalog = Client.open(STAC_URL)
    search = catalog.search(
        collections=[COLLECTION],
        bbox=[float(v) for v in aoi.total_bounds],  # ส่งแค่กรอบสี่เหลี่ยม (ขอบเขตอำเภอจริงมีจุดเยอะเกิน server รับไม่ไหว)
        datetime=DATE_RANGE,
        query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
    )
    items = list(search.items())
    print(f"เจอภาพ {len(items)} ภาพ (เมฆ < {MAX_CLOUD}%)")
    if not items:
        print("ไม่เจอภาพ ลองขยายช่วงวันที่หรือเพิ่ม MAX_CLOUD")
        return
    for it in items:
        print(f"  {it.datetime:%Y-%m-%d}  เมฆ {it.properties['eo:cloud_cover']:.1f}%  {it.id}")

    # 3) โหลดเฉพาะแบนด์ที่ใช้ (red = B04, nir = B08, scl = ชั้นจำแนกเมฆ)
    ds = load(
        items,
        bands=["red", "nir", "scl"],
        bbox=[float(v) for v in aoi.total_bounds],
        crs=CRS,
        resolution=RESOLUTION,
        groupby="solar_day",
        chunks={"x": 2048, "y": 2048},
    )
    print(f"ขนาดข้อมูล: {dict(ds.sizes)}")

    # 4) ตัดเมฆ + แปลงเป็นค่าสะท้อน (scale 0.0001, offset -0.1 ตาม metadata ของ Earth Search v1)
    valid = ds.scl.isin(VALID_SCL)
    red = (ds.red.where(valid & (ds.red > 0)) * 0.0001 - 0.1).clip(min=0)
    nir = (ds.nir.where(valid & (ds.nir > 0)) * 0.0001 - 0.1).clip(min=0)

    # 5) NDVI แล้วรวมทั้งเดือนด้วย median (ช่วยลบเมฆที่หลุดมา)
    ndvi = ((nir - red) / (nir + red)).median(dim="time")
    print("กำลังคำนวณ (ครั้งแรกอาจใช้เวลาหลายนาที ขึ้นกับเน็ต)...")
    ndvi = ndvi.compute()

    # 6) ตัดให้เหลือเฉพาะในขอบเขต 4 อำเภอ
    geobox = ndvi.odc.geobox
    aoi_utm = aoi.to_crs(CRS)
    outside = geometry_mask(aoi_utm.geometry, out_shape=tuple(geobox.shape),
                            transform=geobox.affine)
    ndvi_arr = np.where(outside, np.nan, ndvi.values).astype("float32")

    # 7) บันทึก GeoTIFF (เปิดดูใน QGIS ได้)
    tif_path = OUT_DIR / "ndvi_2026-01.tif"
    with rasterio.open(
        tif_path, "w", driver="GTiff",
        height=geobox.shape[0], width=geobox.shape[1], count=1,
        dtype="float32", crs=CRS, transform=geobox.affine,
        nodata=np.nan, compress="deflate",
    ) as dst:
        dst.write(ndvi_arr, 1)
    print(f"บันทึก {tif_path}")

    # 8) บันทึกภาพ PNG ไว้ดูเร็ว ๆ
    png_path = OUT_DIR / "ndvi_2026-01.png"
    plt.figure(figsize=(10, 10))
    plt.imshow(ndvi_arr, cmap="RdYlGn", vmin=-0.2, vmax=0.9)
    plt.colorbar(label="NDVI", shrink=0.7)
    plt.title(f"NDVI median {DATE_RANGE}")
    plt.axis("off")
    plt.savefig(png_path, dpi=150, bbox_inches="tight")
    print(f"บันทึก {png_path}")

    valid_px = np.isfinite(ndvi_arr)
    print(f"พิกเซลที่ใช้ได้: {valid_px.mean() * 100:.1f}% ของกรอบ, "
          f"NDVI เฉลี่ย = {np.nanmean(ndvi_arr):.3f}")


if __name__ == "__main__":
    main()
