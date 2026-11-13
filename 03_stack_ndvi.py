"""
ขั้นที่ 2.3: รวม NDVI รายเดือน 12 ไฟล์ เป็น GeoTIFF ไฟล์เดียว 12 แบนด์

ใช้: python 03_stack_ndvi.py
ผลลัพธ์: output/ndvi_stack_12m.tif  (แบนด์ 1 = เดือนแรก ... แบนด์ 12 = เดือนสุดท้าย)

เปิดใน QGIS แล้วใช้ Identify คลิกจุดไหนก็ได้ จะเห็นค่า NDVI ครบ 12 เดือนของจุดนั้น
ไฟล์นี้จะเป็น input ของการทำ label และการเทรนโมเดลในขั้นต่อไป
"""
from pathlib import Path

import numpy as np
import rasterio

BASE = Path(__file__).resolve().parent
IN_DIR = BASE / "output" / "monthly"
OUT_FILE = BASE / "output" / "ndvi_stack_12m.tif"


def main():
    tifs = sorted(IN_DIR.glob("ndvi_*.tif"))
    if not tifs:
        print(f"ไม่เจอไฟล์ใน {IN_DIR} - รัน 02_s2_ndvi_monthly.py ก่อน")
        return
    print(f"เจอ {len(tifs)} เดือน: {', '.join(t.stem[5:] for t in tifs)}")

    with rasterio.open(tifs[0]) as ref:
        profile = ref.profile.copy()
        ref_shape, ref_transform, ref_crs = ref.shape, ref.transform, ref.crs

    profile.update(count=len(tifs), dtype="float32", nodata=np.nan,
                   compress="deflate", predictor=3, tiled=True,
                   blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER")

    with rasterio.open(OUT_FILE, "w", **profile) as dst:
        for i, t in enumerate(tifs, start=1):
            with rasterio.open(t) as src:
                if src.shape != ref_shape or src.transform != ref_transform or src.crs != ref_crs:
                    raise ValueError(f"{t.name} มีขนาด/ตำแหน่งไม่ตรงกับไฟล์แรก")
                arr = src.read(1).astype("float32")
            # ล้างค่าเพี้ยนจากเวอร์ชันเก่าของสคริปต์ 02 (ค่า +1, -1 หรือ 0 พอดี = ไม่ใช่ค่าจริง)
            bad = (np.abs(arr) >= 0.9999) | (arr == 0)
            arr[bad] = np.nan
            dst.write(arr, i)
            month = t.stem.replace("ndvi_", "")
            dst.set_band_description(i, month)  # QGIS จะแสดงชื่อเดือนแทน Band 1, 2, ...
            valid = np.isfinite(arr).mean() * 100
            print(f"  แบนด์ {i:2d} = {month}  (ข้อมูลมี {valid:.1f}% ของกรอบ, "
                  f"ล้างค่าเพี้ยน {bad.sum():,} พิกเซล)")

    print(f"\nบันทึก {OUT_FILE}")


if __name__ == "__main__":
    main()
