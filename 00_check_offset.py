"""
ตรวจว่าภาพ Sentinel-2 จาก Earth Search "บวก offset +1000 ไว้จริงไหม"

ใช้: python 00_check_offset.py

หลักคิด: ค่าสะท้อนจริงของพืช/น้ำในแบนด์สีแดงต่ำมาก (ประมาณ 0.01-0.08)
- ถ้าไฟล์บวก offset +1000 ไว้จริง  -> ค่าดิบ (DN) ของพืชจะอยู่ราว 1100-1800 และแทบไม่มีค่าต่ำกว่า 1000
- ถ้าไม่ได้บวกไว้                  -> ค่าดิบของพืชจะอยู่ราว 100-800 (ต่ำกว่า 1000 เป็นส่วนใหญ่)
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
from odc.stac import load
from pystac_client import Client

BASE = Path(__file__).resolve().parent
aoi = gpd.read_file(BASE / "data" / "aoi.geojson").to_crs("EPSG:4326")
bbox = [float(v) for v in aoi.total_bounds]

catalog = Client.open("https://earth-search.aws.element84.com/v1")
# ภาพวันเดียว เมฆน้อยมาก (หน้าแล้ง) - ใช้ความละเอียดหยาบเพื่อให้เร็ว
items = list(catalog.search(collections=["sentinel-2-l2a"], bbox=bbox,
                            datetime="2026-01-05/2026-01-05",
                            query={"eo:cloud_cover": {"lt": 5}}).items())
print(f"ใช้ {len(items)} ภาพ:", ", ".join(i.id for i in items))
for it in items[:3]:
    print("  ", it.id, "baseline", it.properties.get("s2:processing_baseline"),
          "| offset ใน metadata:", it.assets["red"].extra_fields.get("raster:bands", [{}])[0].get("offset"))

ds = load(items, bands=["red", "nir", "scl"], bbox=bbox, crs="EPSG:32647",
          resolution=60, groupby="solar_day").isel(time=0).compute()

red, scl = ds.red.values.astype(float), ds.scl.values
for name, cls in [("พืช (SCL=4)", 4), ("น้ำ (SCL=6)", 6), ("ดินเปล่า (SCL=5)", 5)]:
    v = red[(scl == cls) & (red > 0)]
    if v.size == 0:
        print(f"{name}: ไม่มีพิกเซล")
        continue
    p = np.percentile(v, [1, 10, 50, 90])
    below = (v < 1000).mean() * 100
    print(f"{name}: DN แดง p1={p[0]:.0f} p10={p[1]:.0f} p50={p[2]:.0f} p90={p[3]:.0f} "
          f"| ต่ำกว่า 1000 = {below:.1f}%")

veg = red[(scl == 4) & (red > 0)]
if veg.size:
    if np.median(veg) < 1000:
        print("\nสรุป: ค่าดิบของพืชส่วนใหญ่ต่ำกว่า 1000 -> ข้อมูล 'ไม่ได้' บวก offset ไว้ ต้องตั้ง OFFSET = 0")
    else:
        print("\nสรุป: ค่าดิบของพืชอยู่เหนือ 1000 -> ข้อมูลบวก offset ไว้จริง OFFSET = -0.1 ถูกต้อง")
