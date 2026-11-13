"""
06_web_app.py — เว็บดูผลการจำแนกพืช (FastAPI หลังบ้าน + MapLibre หน้าบ้าน)

รัน:
    .venv\\Scripts\\activate
    pip install fastapi uvicorn      (ครั้งแรกครั้งเดียว)
    python 06_web_app.py
แล้วเปิด http://127.0.0.1:8000

ใช้ไฟล์จาก 05_train_rf.py:
    output/model/crop_map.tif     แผนที่ชนิดพืช (uint8, 0 = ไม่มีข้อมูล, มี colormap ฝังมา)
    output/model/confidence.tif   ความมั่นใจ 0-100
    output/model/classes.csv      รหัส -> ชื่อคลาส
    output/ndvi_stack_12m.tif     NDVI 12 เดือน (ไว้ดูกราฟตอนคลิกจุด)
    output/label_points_ndvi.csv  ค่า NDVI ที่จุด label (ไว้ทำเส้นเฉลี่ยของแต่ละคลาส)
    data/aoi.geojson              ขอบเขต 4 อำเภอ

ทำไมทำ tile เอง แทนการส่งภาพทั้งก้อนไปให้เบราว์เซอร์:
    แผนที่มีขนาด 7240 x 7662 พิกเซล ใหญ่เกินขนาด texture สูงสุดของการ์ดจอบางรุ่น
    เลยแปลงเป็น Web Mercator (EPSG:3857) เก็บไว้ในหน่วยความจำครั้งเดียวตอนเปิดเซิร์ฟเวอร์
    แล้วตัดเป็น tile 256x256 ตามที่แผนที่ขอ (หลักการเดียวกับ TiTiler แต่ไม่ต้องติดตั้งเพิ่ม)

หมายเหตุ: พื้นที่ที่แสดงมาจากการนับพิกเซล ยังไม่ได้ปรับตามความคลาดเคลื่อนของโมเดล
          (ปรับตามแนว Olofsson et al. 2014 ในงานข้อ 5)
"""
import io
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from matplotlib import colormaps
from PIL import Image
from rasterio.features import rasterize
from rasterio.warp import (Resampling, calculate_default_transform, reproject,
                           transform as warp_transform)
from rasterio.windows import Window
from shapely.geometry import Point

# โฟลเดอร์โปรเจกต์ = โฟลเดอร์ของไฟล์นี้ (เปลี่ยนได้ด้วยตัวแปรแวดล้อม CROP_AI_DIR)
BASE = Path(os.environ.get("CROP_AI_DIR", Path(__file__).resolve().parent))
MODEL_DIR = BASE / "output" / "model"
CROP_TIF = MODEL_DIR / "crop_map.tif"
CONF_TIF = MODEL_DIR / "confidence.tif"
CLASSES_CSV = MODEL_DIR / "classes.csv"
STACK_TIF = BASE / "output" / "ndvi_stack_12m.tif"
LABEL_CSV = BASE / "output" / "label_points_ndvi.csv"
AOI_FILE = BASE / "data" / "aoi.geojson"
WEB_DIR = Path(__file__).resolve().parent / "web"

TEST_DISTRICT = "ครบุรี"          # พื้นที่ทดสอบ (spatial split)
TILE = 256
HALF_WORLD = 20037508.342789244   # ครึ่งความกว้างโลกใน EPSG:3857 (เมตร)
RAI_M2 = 1600                     # 1 ไร่ = 1,600 ตร.ม.

NAME_TH = {"rice": "ข้าว", "sugarcane": "อ้อย", "cassava": "มันสำปะหลัง",
           "maize": "ข้าวโพด", "forest": "ป่าไม้", "orchard": "สวนผลไม้",
           "water": "แหล่งน้ำ", "urban": "ชุมชน/สิ่งปลูกสร้าง", "other": "อื่น ๆ",
           "unsure": "ไม่แน่ใจ", "umsure": "ไม่แน่ใจ (สะกดผิด umsure)"}
# สีสำรอง ใช้เมื่อ crop_map.tif ไม่มี colormap (ชุดเดียวกับใน 05_train_rf.py)
FALLBACK_COLORS = {"rice": (31, 119, 180), "sugarcane": (44, 160, 44), "cassava": (255, 127, 14),
                   "maize": (188, 189, 34), "forest": (23, 100, 60), "orchard": (227, 119, 194),
                   "water": (30, 60, 200), "urban": (140, 86, 75), "other": (148, 103, 189)}


# ---------------------------------------------------------------- โหลดข้อมูลครั้งเดียวตอนเริ่ม
def to_web_mercator(arr, src_transform, src_crs, bounds):
    """แปลงราสเตอร์เป็น EPSG:3857 ด้วย nearest (ห้ามใช้ bilinear เพราะรหัสคลาสเอามาเฉลี่ยกันไม่ได้)"""
    h, w = arr.shape
    dst_t, dst_w, dst_h = calculate_default_transform(src_crs, "EPSG:3857", w, h, *bounds)
    out = np.zeros((dst_h, dst_w), dtype="uint8")
    reproject(arr, out, src_transform=src_transform, src_crs=src_crs, src_nodata=0,
              dst_transform=dst_t, dst_crs="EPSG:3857", dst_nodata=0,
              resampling=Resampling.nearest)
    return out, dst_t


def district_stats(crop, conf, transform, crs, aoi, classes):
    """นับพื้นที่แต่ละคลาสในแต่ละอำเภอ (จำนวนพิกเซล x พื้นที่ต่อพิกเซล)"""
    aoi_utm = aoi.to_crs(crs)
    dist = rasterize(((geom, i + 1) for i, geom in enumerate(aoi_utm.geometry)),
                     out_shape=crop.shape, transform=transform, fill=0, dtype="uint8")
    px_m2 = abs(transform.a * transform.e)
    n = len(aoi) + 1
    # รวมรหัสอำเภอกับรหัสคลาสเป็นตัวเลขเดียว แล้วนับทีเดียวด้วย bincount (เร็วกว่าวนลูปมาก)
    counts = np.bincount((dist.astype("uint16") * 256 + crop).ravel(),
                         minlength=n * 256).reshape(n, 256)
    out = []
    for i, name in enumerate(aoi["adm2_name1"], start=1):
        m = (dist == i) & (crop > 0)
        areas = [{"code": code, "crop": cname,
                  "km2": int(counts[i, code]) * px_m2 / 1e6,
                  "rai": int(counts[i, code]) * px_m2 / RAI_M2}
                 for code, cname in classes.items()]
        out.append({"name": name, "is_test": name == TEST_DISTRICT,
                    "mapped_km2": int(m.sum()) * px_m2 / 1e6,
                    "mean_conf": float(conf[m].mean()) if m.any() else None,
                    "classes": areas})
    return out


def class_curves(classes):
    """เส้น NDVI เฉลี่ยของแต่ละคลาสจากจุด label (ไว้เทียบกับพิกเซลที่คลิก)"""
    if not LABEL_CSV.exists():
        return {}
    df = pd.read_csv(LABEL_CSV)
    months = [c for c in df.columns if c[:2] == "20" and "-" in c]
    res = {}
    for cname in classes.values():
        sub = df.loc[df["crop"] == cname, months]
        if len(sub):
            res[cname] = [None if np.isnan(v) else round(float(v), 4) for v in sub.mean()]
    return res


def load_all():
    t0 = time.time()
    cls_df = pd.read_csv(CLASSES_CSV)
    classes = {int(c): n for c, n in zip(cls_df["code"], cls_df["crop"])}

    with rasterio.open(CROP_TIF) as src:
        crop = src.read(1)
        transform, crs, bounds = src.transform, src.crs, src.bounds
        try:
            cmap = src.colormap(1)
        except ValueError:
            cmap = {}
    with rasterio.open(CONF_TIF) as src:
        conf = src.read(1)
    with rasterio.open(STACK_TIF) as src:
        months = [d or f"band{i + 1}" for i, d in enumerate(src.descriptions)]

    aoi = gpd.read_file(AOI_FILE)
    stats = district_stats(crop, conf, transform, crs, aoi, classes)

    crop_m, merc_t = to_web_mercator(crop, transform, crs, bounds)
    conf_m, _ = to_web_mercator(conf, transform, crs, bounds)
    del crop, conf

    colors = {}
    for code, cname in classes.items():
        rgb = tuple(cmap.get(code, (0, 0, 0, 0))[:3])
        if rgb == (0, 0, 0):
            rgb = FALLBACK_COLORS.get(cname, (200, 200, 200))
        colors[code] = tuple(int(v) for v in rgb)

    # ขอบเขตข้อมูลเป็น lon/lat ให้ MapLibre ขอ tile เฉพาะบริเวณที่มีข้อมูล
    xs, ys = warp_transform(crs, "EPSG:4326",
                            [bounds.left, bounds.right, bounds.left, bounds.right],
                            [bounds.bottom, bounds.bottom, bounds.top, bounds.top])
    lonlat_bounds = [min(xs), min(ys), max(xs), max(ys)]

    print(f"โหลดข้อมูลเสร็จใน {time.time() - t0:.1f} วินาที "
          f"(Web Mercator {crop_m.shape[1]} x {crop_m.shape[0]} พิกเซล)")
    return dict(classes=classes, colors=colors, crs=crs, months=months, aoi=aoi,
                stats=stats, crop_m=crop_m, conf_m=conf_m, merc_t=merc_t,
                bounds=lonlat_bounds, curves=class_curves(classes),
                px_m2=abs(transform.a * transform.e))


D = load_all()


# ---------------------------------------------------------------- จานสีของ PNG
def palette_from(colors):
    pal = [0] * 768
    for code, rgb in colors.items():
        pal[code * 3: code * 3 + 3] = rgb
    return pal


CROP_PAL = palette_from(D["colors"])
# ความมั่นใจ 1-100: แดง (ต่ำ = ควรไป label เพิ่ม) -> เหลือง -> เขียว (สูง)
_rdylgn = colormaps["RdYlGn"](np.linspace(0, 1, 101))[:, :3] * 255
CONF_PAL = palette_from({v: tuple(int(c) for c in _rdylgn[v]) for v in range(1, 101)})


def encode_png(arr, pal):
    """PNG แบบ palette (8 บิต) ไฟล์เล็ก และให้รหัส 0 เป็นสีโปร่งใส"""
    img = Image.frombytes("P", (arr.shape[1], arr.shape[0]), np.ascontiguousarray(arr).tobytes())
    img.putpalette(pal)
    buf = io.BytesIO()
    img.save(buf, "PNG", transparency=0)
    return buf.getvalue()


EMPTY_TILE = encode_png(np.zeros((TILE, TILE), "uint8"), CROP_PAL)


@lru_cache(maxsize=4096)
def render_tile(layer, z, x, y, show_key):
    arr = D["crop_m"] if layer == "crop" else D["conf_m"]
    t = D["merc_t"]
    h, w = arr.shape

    # ขอบ tile ใน EPSG:3857 -> หาพิกเซลต้นทางที่ตรงกับจุดกึ่งกลางของแต่ละพิกเซลใน tile (nearest)
    size = 2 * HALF_WORLD / 2 ** z
    minx, maxy = -HALF_WORLD + x * size, HALF_WORLD - y * size
    centers = (np.arange(TILE) + 0.5) * size / TILE
    cols = np.floor((minx + centers - t.c) / t.a).astype(np.int64)
    rows = np.floor((t.f - (maxy - centers)) / -t.e).astype(np.int64)
    ok_r, ok_c = (rows >= 0) & (rows < h), (cols >= 0) & (cols < w)
    if not ok_r.any() or not ok_c.any():
        return EMPTY_TILE

    out = arr[np.clip(rows, 0, h - 1)[:, None], np.clip(cols, 0, w - 1)[None, :]]
    out[~(ok_r[:, None] & ok_c[None, :])] = 0

    if layer == "crop":
        if show_key is not None:              # ซ่อนคลาสที่ไม่ได้ติ๊ก
            lut = np.zeros(256, "uint8")
            for code in map(int, filter(None, show_key.split(","))):
                lut[code] = code
            out = lut[out]
        return encode_png(out, CROP_PAL)
    return encode_png(out, CONF_PAL)


# ---------------------------------------------------------------- API
app = FastAPI(title="AI Crop Intelligence")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/meta")
def meta():
    return {
        "bounds": D["bounds"],
        "months": D["months"],
        "pixel_area_m2": D["px_m2"],
        "test_district": TEST_DISTRICT,
        "classes": [{"code": c, "name": n, "name_th": NAME_TH.get(n, n),
                     "color": "#%02x%02x%02x" % D["colors"][c]}
                    for c, n in D["classes"].items()],
        "districts": D["stats"],
        "curves": D["curves"],
    }


@app.get("/api/aoi")
def aoi():
    g = D["aoi"][["adm2_name1", "geometry"]].copy()
    g["is_test"] = g["adm2_name1"] == TEST_DISTRICT
    return Response(g.to_json(), media_type="application/json")


@app.get("/tiles/{layer}/{z}/{x}/{y}.png")
def tile(layer: str, z: int, x: int, y: int, show: Optional[str] = None):
    if layer not in ("crop", "confidence") or not (0 <= z <= 22):
        raise HTTPException(404)
    show_key = None
    if show is not None:
        codes = [s for s in show.split(",") if s]
        if not all(s.isdigit() for s in codes):
            raise HTTPException(400, "show ต้องเป็นรหัสคลาสคั่นด้วยจุลภาค เช่น 1,4,5")
        show_key = ",".join(sorted(codes, key=int))
    return Response(render_tile(layer, z, x, y, show_key), media_type="image/png")


@app.get("/api/point")
def point(lon: float = Query(...), lat: float = Query(...)):
    """ค่าของพิกเซลที่คลิก: คลาส, ความมั่นใจ, NDVI 12 เดือน, อำเภอ"""
    xs, ys = warp_transform("EPSG:4326", D["crs"], [lon], [lat])
    # เปิดไฟล์ใหม่ทุกครั้ง เพราะ rasterio dataset ใช้ร่วมกันข้ามเธรดไม่ปลอดภัย
    with rasterio.open(CROP_TIF) as s:
        row, col = s.index(xs[0], ys[0])
        if not (0 <= row < s.height and 0 <= col < s.width):
            return {"inside": False}
        win = Window(col, row, 1, 1)
        code = int(s.read(1, window=win)[0, 0])
    with rasterio.open(CONF_TIF) as s:
        conf = int(s.read(1, window=win)[0, 0])
    with rasterio.open(STACK_TIF) as s:
        ndvi = s.read(window=win)[:, 0, 0]

    hit = D["aoi"][D["aoi"].contains(Point(lon, lat))]
    name = D["classes"].get(code)
    return {
        "inside": True,
        "lon": lon, "lat": lat, "row": int(row), "col": int(col),
        "district": hit["adm2_name1"].iloc[0] if len(hit) else None,
        "code": code,
        "crop": name,
        "crop_th": NAME_TH.get(name, name) if name else None,
        "confidence": conf if code else None,
        "ndvi": [None if np.isnan(v) else round(float(v), 4) for v in ndvi],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
