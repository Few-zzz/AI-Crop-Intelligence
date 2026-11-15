"""
หลังบ้านของระบบที่ deploy (FastAPI)
อ่านข้อมูลจากฐานข้อมูล PostGIS (map_runs, area_stats, districts, crop_classes)
และอ่านค่าจากไฟล์ COG ของแต่ละฤดูกาล ส่วน tile แผนที่ชนิดพืช/ความเชื่อมั่นให้ TiTiler ทำ

รันบนเครื่อง (ต้องเปิด docker compose up -d db ก่อน):
    .venv\\Scripts\\activate
    uvicorn api.main:app --port 8001 --reload
"""
import csv
import io
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import psycopg
import rasterio
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from PIL import Image
from psycopg.rows import dict_row
from rasterio.enums import Resampling
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window
from rio_tiler.io import Reader


def _load_dotenv():
    """ตอนรันบนเครื่อง อ่านรหัสผ่านจาก .env ของโปรเจกต์ (ใน container ใช้ environment แทน)"""
    f = Path(__file__).resolve().parent.parent / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
# ใช้ 127.0.0.1 ไม่ใช่ localhost: บน Windows localhost ลอง IPv6 ก่อนแล้วค้างนาน
DATABASE_URL = os.environ.get("DATABASE_URL") or (
    f"postgresql://{os.environ.get('POSTGRES_USER', 'cropai')}:{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@{os.environ.get('POSTGRES_HOST', '127.0.0.1')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ.get('POSTGRES_DB', 'cropai')}")
# โฟลเดอร์ที่มี seasons/<ปี>/... (ใน container = /data, บนเครื่อง = output/)
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "output"))
TITILER_DATA_DIR = "/data"          # path ที่ container ของ TiTiler mount ไฟล์ไว้

app = FastAPI(title="AI Crop Intelligence API")


def q(sql, params=(), one=False):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5) as conn:
        rows = conn.execute(sql, params).fetchall()
    return (rows[0] if rows else None) if one else rows


def get_run(run_id):
    r = q("SELECT * FROM map_runs WHERE id = %s", (run_id,), one=True)
    if not r:
        raise HTTPException(404, f"ไม่มีฤดูกาล id={run_id}")
    return r


def class_colors():
    return {r["name"]: r for r in q("SELECT name, name_th, color FROM crop_classes")}


# ---------------------------------------------------------------- ข้อมูลพื้นฐาน
@app.get("/api/health")
def health():
    q("SELECT 1")
    return {"ok": True}


@app.get("/api/classes")
def classes():
    return q("SELECT id, name, name_th, color FROM crop_classes ORDER BY id")


@app.get("/api/districts")
def districts():
    rows = q("""SELECT id, name_th, province, role,
                       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0003), 5)::json AS geometry
                FROM districts ORDER BY id""")
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": r.pop("geometry"), "properties": r} for r in rows]}


@app.get("/api/seasons")
def seasons():
    """ทุกฤดูกาลที่ทำนายแล้ว พร้อม path ที่ TiTiler ใช้อ่านไฟล์"""
    rows = q("""SELECT id, season, season_label, month_start, month_end, months_with_data,
                       model_name, model_version, model_macro_f1, model_accuracy, season_macro_f1,
                       class_codes, crop_map_path, confidence_path, bounds, pixel_area_m2, created_at
                FROM map_runs ORDER BY season, created_at""")
    for r in rows:
        r["crop_cog"] = f"{TITILER_DATA_DIR}/{r.pop('crop_map_path')}"
        r["confidence_cog"] = f"{TITILER_DATA_DIR}/{r.pop('confidence_path')}"
    return rows


# ---------------------------------------------------------------- สถิติพื้นที่
@app.get("/api/runs/{run_id}/stats")
def run_stats(run_id: int):
    get_run(run_id)
    return q("""SELECT d.name_th AS district, d.role, c.name AS class, c.name_th, c.color,
                       s.pixel_count, s.area_km2, s.adj_area_km2, s.ci95_km2, s.mean_conf
                FROM area_stats s JOIN districts d ON d.id = s.district_id
                JOIN crop_classes c ON c.id = s.class_id
                WHERE s.run_id = %s ORDER BY d.id, s.area_km2 DESC""", (run_id,))


@app.get("/api/trend")
def trend(district: str | None = None):
    """พื้นที่แต่ละคลาสในทุกฤดูกาล (ใช้วาดกราฟแนวโน้ม)"""
    return q("""SELECT r.id AS run_id, r.season, r.season_label, c.name AS class, c.name_th, c.color,
                       SUM(s.area_km2) AS area_km2
                FROM area_stats s JOIN map_runs r ON r.id = s.run_id
                JOIN crop_classes c ON c.id = s.class_id JOIN districts d ON d.id = s.district_id
                WHERE %(d)s::text IS NULL OR d.name_th = %(d)s
                GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY r.season, area_km2 DESC""", {"d": district})


@lru_cache(maxsize=16)
def _transitions(path_a, path_b, codes_a, codes_b, px_km2):
    """นับพื้นที่ที่เปลี่ยนชนิดพืชระหว่างสองฤดูกาล (ไฟล์ต้องเป็นกริดเดียวกัน)"""
    with rasterio.open(path_a) as a, rasterio.open(path_b) as b:
        if a.shape != b.shape or a.transform != b.transform:
            return None
        ca, cb = a.read(1), b.read(1)
    ok = (ca > 0) & (cb > 0)
    cnt = np.bincount(ca[ok].astype("uint16") * 256 + cb[ok], minlength=256 * 256)
    na, nb = dict(codes_a), dict(codes_b)
    out = []
    for i in np.flatnonzero(cnt):
        fa, tb = na.get(str(i // 256)), nb.get(str(i % 256))
        if fa != tb:
            out.append({"from": fa, "to": tb, "area_km2": float(cnt[i] * px_km2)})
    return {"changed_km2": sum(t["area_km2"] for t in out), "total_km2": float(ok.sum() * px_km2),
            "top": sorted(out, key=lambda t: -t["area_km2"])[:12]}


@app.get("/api/compare")
def compare(a: int, b: int, district: str | None = None):
    """เทียบพื้นที่แต่ละคลาสระหว่างสองฤดูกาล + การเปลี่ยนชนิดพืชที่พบมากที่สุด"""
    ra, rb = get_run(a), get_run(b)
    rows = q("""SELECT c.name AS class, c.name_th, c.color,
                       SUM(s.area_km2) FILTER (WHERE s.run_id = %(a)s) AS area_a,
                       SUM(s.area_km2) FILTER (WHERE s.run_id = %(b)s) AS area_b
                FROM area_stats s JOIN crop_classes c ON c.id = s.class_id
                JOIN districts d ON d.id = s.district_id
                WHERE s.run_id IN (%(a)s, %(b)s) AND (%(d)s::text IS NULL OR d.name_th = %(d)s)
                GROUP BY 1, 2, 3 ORDER BY 5 DESC NULLS LAST""", {"a": a, "b": b, "d": district})
    for r in rows:
        r["area_a"], r["area_b"] = r["area_a"] or 0.0, r["area_b"] or 0.0
        r["diff_km2"] = r["area_b"] - r["area_a"]
        r["diff_pct"] = (r["diff_km2"] / r["area_a"] * 100) if r["area_a"] else None
    trans = None
    if district is None:
        trans = _transitions(str(DATA_DIR / ra["crop_map_path"]), str(DATA_DIR / rb["crop_map_path"]),
                             tuple(sorted(ra["class_codes"].items())), tuple(sorted(rb["class_codes"].items())),
                             ra["pixel_area_m2"] / 1e6)
    return {"a": {"id": a, "label": ra["season_label"]}, "b": {"id": b, "label": rb["season_label"]},
            "classes": rows, "transitions": trans}


# ---------------------------------------------------------------- tile ของพิกเซลที่เปลี่ยนชนิดพืช
def _png(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


EMPTY_PNG = _png(np.zeros((256, 256, 4), "uint8"))


@app.get("/api/change/{a}/{b}/{z}/{x}/{y}.png")
def change_tile(a: int, b: int, z: int, x: int, y: int):
    """พิกเซลที่ชนิดพืชต่างกันระหว่างฤดูกาล a กับ b ระบายด้วยสีของคลาสในฤดูกาล b"""
    ra, rb = get_run(a), get_run(b)
    try:
        with Reader(str(DATA_DIR / ra["crop_map_path"])) as sa, Reader(str(DATA_DIR / rb["crop_map_path"])) as sb:
            if not sa.tile_exists(x, y, z):
                return Response(EMPTY_PNG, media_type="image/png")
            ta = sa.tile(x, y, z, resampling_method="nearest").data[0]
            tb = sb.tile(x, y, z, resampling_method="nearest").data[0]
    except Exception:
        return Response(EMPTY_PNG, media_type="image/png")
    cols = class_colors()
    names_a = np.array([ra["class_codes"].get(str(i), "") for i in range(256)])
    names_b = np.array([rb["class_codes"].get(str(i), "") for i in range(256)])
    changed = (ta > 0) & (tb > 0) & (names_a[ta] != names_b[tb])
    rgba = np.zeros((*tb.shape, 4), "uint8")
    for code, name in rb["class_codes"].items():
        hexc = cols.get(name, {}).get("color", "#c8c8c8")
        rgba[changed & (tb == int(code))] = [int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16), 255]
    return Response(_png(rgba), media_type="image/png")


# ---------------------------------------------------------------- ดาวน์โหลดผลของแต่ละฤดูกาล
RAI_PER_KM2 = 625


def _fname(run, name, ext):
    return f"{name}_{run['season_label'].replace('/', '-')}.{ext}"   # เช่น crop_map_2568-69.tif


@app.get("/api/runs/{run_id}/download/{kind}.tif")
def download_tif(run_id: int, kind: str):
    """ไฟล์ GeoTIFF (COG) ต้นฉบับ เปิดใน QGIS ได้ทันที (crop_map มีตารางสีฝังอยู่แล้ว)"""
    col = {"crop_map": "crop_map_path", "confidence": "confidence_path"}.get(kind)
    if not col:
        raise HTTPException(404, "มีแค่ crop_map หรือ confidence")
    r = get_run(run_id)
    path = DATA_DIR / r[col]
    if not path.exists():
        raise HTTPException(404, "ไม่พบไฟล์ผลลัพธ์ของฤดูกาลนี้")
    return FileResponse(path, media_type="image/tiff", filename=_fname(r, kind, "tif"))


@app.get("/api/runs/{run_id}/download/stats.csv")
def download_stats(run_id: int):
    """พื้นที่และสัดส่วน (%) ของแต่ละชนิดพืช รายอำเภอ + รวมทุกอำเภอ (เปิดใน Excel ได้ ภาษาไทยไม่เพี้ยน)"""
    r = get_run(run_id)
    rows = run_stats(run_id)
    groups = {}
    for s in rows:
        groups.setdefault(s["district"], []).append(s)
    total = {}
    for s in rows:
        t = total.setdefault(s["class"], {**s, "district": "รวมทุกอำเภอ", "role": "", "pixel_count": 0,
                                          "area_km2": 0.0, "_conf": 0.0})
        t["pixel_count"] += s["pixel_count"]
        t["area_km2"] += s["area_km2"]
        t["_conf"] += (s["mean_conf"] or 0) * s["pixel_count"]
    for t in total.values():
        t["mean_conf"] = t["_conf"] / t["pixel_count"] if t["pixel_count"] else None
    groups["รวมทุกอำเภอ"] = sorted(total.values(), key=lambda t: -t["area_km2"])

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ฤดูกาล", "อำเภอ", "บทบาท", "ชนิดพืช", "class", "จำนวนพิกเซล", "พื้นที่ (ตร.กม.)",
                "พื้นที่ (ไร่)", "สัดส่วนในอำเภอ (%)", "ความมั่นใจเฉลี่ย (%)",
                "โมเดล", "Macro-F1 ของโมเดล", "เดือนที่มีภาพ"])
    role_th = {"train": "เทรน", "test": "ทดสอบ"}
    for dist, items in groups.items():
        dsum = sum(s["area_km2"] for s in items) or 1
        for s in items:
            w.writerow([r["season_label"], dist, role_th.get(s["role"], s["role"]), s["name_th"], s["class"],
                        s["pixel_count"], round(s["area_km2"], 3), round(s["area_km2"] * RAI_PER_KM2),
                        round(100 * s["area_km2"] / dsum, 2),
                        round(s["mean_conf"], 1) if s["mean_conf"] is not None else "",
                        f"{r['model_name']} {r['model_version']}",
                        round(r["model_macro_f1"], 3) if r["model_macro_f1"] is not None else "",
                        r["months_with_data"]])
    # utf-8-sig (มี BOM) ให้ Excel รู้ว่าเป็น UTF-8 ไม่งั้นภาษาไทยเพี้ยน
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_fname(r, "area_stats", "csv")}"'})


@app.get("/api/runs/{run_id}/download/map.png")
def download_png(run_id: int, size: int = Query(2400, ge=256, le=8000)):
    """ภาพแผนที่ชนิดพืช (PNG พื้นโปร่งใส) ย่อให้ด้านยาวไม่เกิน size พิกเซล ใช้แปะรายงาน/สไลด์"""
    r = get_run(run_id)
    with rasterio.open(DATA_DIR / r["crop_map_path"]) as s:
        scale = min(1.0, size / max(s.width, s.height))
        shape = (max(1, int(s.height * scale)), max(1, int(s.width * scale)))
        codes = s.read(1, out_shape=shape, resampling=Resampling.nearest)
    cols = class_colors()
    lut = np.zeros((256, 4), "uint8")
    for code, name in r["class_codes"].items():
        hexc = cols.get(name, {}).get("color", "#c8c8c8")
        lut[int(code)] = [int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16), 255]
    return Response(_png(lut[codes]), media_type="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{_fname(r, "crop_map", "png")}"'})


# ---------------------------------------------------------------- คลิกดูพิกเซล
@app.get("/api/point")
def point(run: int, lon: float = Query(...), lat: float = Query(...)):
    r = get_run(run)
    d = q("""SELECT name_th, role FROM districts
             WHERE ST_Contains(geom, ST_SetSRID(ST_Point(%s, %s), 4326))""", (lon, lat), one=True)
    with rasterio.open(DATA_DIR / r["crop_map_path"]) as s:
        xs, ys = warp_transform("EPSG:4326", s.crs, [lon], [lat])
        row, col = s.index(xs[0], ys[0])
        if not (0 <= row < s.height and 0 <= col < s.width):
            return {"inside": False}
        win = Window(col, row, 1, 1)
        code = int(s.read(1, window=win)[0, 0])
    with rasterio.open(DATA_DIR / r["confidence_path"]) as s:
        conf = int(s.read(1, window=win)[0, 0])
    with rasterio.open(DATA_DIR / r["ndvi_path"]) as s:
        ndvi = s.read(window=win)[:, 0, 0]
        months = list(s.descriptions)
    name = r["class_codes"].get(str(code))
    cls = class_colors().get(name) if name else None
    return {"inside": True, "lon": lon, "lat": lat, "district": d["name_th"] if d else None,
            "district_role": d["role"] if d else None,
            "class": name, "name_th": cls["name_th"] if cls else None, "color": cls["color"] if cls else None,
            "confidence": conf if code else None, "months": months,
            "ndvi": [None if np.isnan(v) else round(float(v), 4) for v in ndvi]}
