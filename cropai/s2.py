"""ดึง NDVI รายเดือนจาก Sentinel-2 (ตรรกะเดียวกับ 02_s2_ndvi_monthly.py แต่กำหนดเดือนได้)"""
import numpy as np
import pandas as pd
import rasterio
from odc.stac import load
from rasterio.features import geometry_mask

from .config import COLLECTION, CRS, MAX_CLOUD, OFFSET, RESOLUTION, SCALE, VALID_SCL


def month_ndvi(catalog, aoi, month: pd.Period, mask_cache: dict):
    """median NDVI ของเดือนนั้น ตัดเมฆด้วย SCL และตัดพิกเซลนอก AOI -> (array, geobox) หรือ (None, None)"""
    date_range = f"{month.start_time:%Y-%m-%d}/{month.end_time:%Y-%m-%d}"
    bbox = [float(v) for v in aoi.total_bounds]           # ใช้ bbox ไม่ใช่ intersects (polygon ซับซ้อนเกิน)
    items = list(catalog.search(collections=[COLLECTION], bbox=bbox, datetime=date_range,
                                query={"eo:cloud_cover": {"lt": MAX_CLOUD}}).items())
    print(f"[{month}] เจอ {len(items)} ภาพ", flush=True)
    if not items:
        return None, None
    ds = load(items, bands=["red", "nir", "scl"], bbox=bbox, crs=CRS, resolution=RESOLUTION,
              groupby="solar_day", chunks={"x": 2048, "y": 2048})
    red = ds.red * SCALE + OFFSET
    nir = ds.nir * SCALE + OFFSET
    ok = ds.scl.isin(VALID_SCL) & (red > 0) & (nir > 0)
    ndvi = ((nir - red) / (nir + red)).where(ok).median(dim="time").compute()
    gb = ndvi.odc.geobox
    key = (tuple(gb.shape), tuple(gb.affine))
    if key not in mask_cache:
        mask_cache[key] = geometry_mask(aoi.to_crs(CRS).geometry, out_shape=tuple(gb.shape),
                                        transform=gb.affine)
    arr = np.where(mask_cache[key], np.nan, ndvi.values).astype("float32")
    return arr, gb


def save_month(path, arr, geobox):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype="float32", crs=CRS, transform=geobox.affine, nodata=np.nan,
                       compress="deflate") as dst:
        dst.write(arr, 1)
