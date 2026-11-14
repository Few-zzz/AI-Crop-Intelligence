"""
ขั้นที่ 7: ทำนายชนิดพืชทั้งฤดูกาลเพาะปลูกด้วยโมเดลที่เทรนแล้ว แล้วบันทึกลงฐานข้อมูล

ฤดูกาล = พ.ค. ของปีที่ระบุ ถึง เม.ย. ของปีถัดไป (เหมือนข้อมูลที่ใช้เทรน)
    --season 2024  ->  พ.ค. 2024 – เม.ย. 2025  (ฤดูกาล 2567/68)

ใช้:
    python 07_predict.py --season 2024                  ดึงภาพ 12 เดือน (รันซ้ำได้ ข้ามเดือนที่มีแล้ว) -> ทำนาย -> ลง DB
    python 07_predict.py --season 2025 --stack output/ndvi_stack_12m.tif
                                                        ใช้ NDVI ที่มีอยู่แล้ว ไม่ต้องดึงภาพใหม่
    python 07_predict.py --season 2024 --no-db          ทำแค่ไฟล์ ไม่บันทึกลงฐานข้อมูล

ผลลัพธ์: output/seasons/<ปี>/
    monthly/ndvi_YYYY-MM.tif   NDVI รายเดือน
    ndvi_stack.tif             NDVI 12 แบนด์ (COG)
    crop_map.tif               แผนที่ชนิดพืช (COG, 0 = ไม่มีข้อมูล)
    confidence.tif             ความมั่นใจ 0-100 (COG)
    summary.json               สรุปพื้นที่แต่ละคลาสต่ออำเภอ
และตาราง map_runs + area_stats ในฐานข้อมูล (ต้องเปิด docker compose up -d db ก่อน)

หมายเหตุ: โมเดลเทรนจาก label ฤดูกาล 2568/69 เท่านั้น ความแม่นในฤดูกาลอื่นยังไม่ได้วัด
          (พืชหมุนเวียน/สภาพอากาศต่างกัน) ควร label ตรวจสอบชุดเล็ก ๆ ของปีนั้นก่อนใช้ตัวเลขจริง
"""
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.shutil import copy as rio_copy
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from cropai.config import (BASE, COLORS, DEFAULT_COLOR, MODEL_FILE, MODEL_REPORT, SEASON_MONTHS,
                           SEASON_START_MONTH, SEASONS_DIR, STAC_URL, AOI_FILE, season_label)
from cropai.features import fill_gaps


def season_months(year):
    return pd.period_range(f"{year}-{SEASON_START_MONTH:02d}", periods=SEASON_MONTHS, freq="M")


# ---------------------------------------------------------------- 1) NDVI รายเดือน
def download_months(year, out_dir, aoi):
    from pystac_client import Client          # import ตรงนี้ เผื่อใช้ --stack โดยไม่มีเน็ต
    from cropai.s2 import month_ndvi, save_month
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = Client.open(STAC_URL)
    cache = {}
    for m in season_months(year):
        tif = out_dir / f"ndvi_{m}.tif"
        if tif.exists():
            print(f"[{m}] มีไฟล์แล้ว ข้าม")
            continue
        t0 = time.time()
        arr, gb = month_ndvi(catalog, aoi, m, cache)
        if arr is None:
            print(f"[{m}] ไม่มีภาพ (จะเติมช่องว่างตอนทำนาย)")
            continue
        save_month(tif, arr, gb)
        print(f"[{m}] บันทึกแล้ว ({time.time() - t0:.0f} วินาที)", flush=True)


def build_stack(year, monthly_dir, out_tif):
    """รวม 12 เดือนเป็นไฟล์เดียว เดือนที่ไม่มีภาพใส่ NaN ทั้งแบนด์ (ลำดับแบนด์ต้องตรงกับตอนเทรน)"""
    months = [str(m) for m in season_months(year)]
    tifs = {m: monthly_dir / f"ndvi_{m}.tif" for m in months}
    have = [m for m in months if tifs[m].exists()]
    if not have:
        sys.exit(f"ไม่มี NDVI รายเดือนใน {monthly_dir} เลย")
    with rasterio.open(tifs[have[0]]) as ref:
        prof = ref.profile.copy()
    prof.update(count=len(months), dtype="float32", nodata=np.nan, compress="deflate", predictor=3,
                tiled=True, blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER")
    tmp = out_tif.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", **prof) as dst:
        for i, m in enumerate(months, 1):
            if m in have:
                with rasterio.open(tifs[m]) as src:
                    if src.shape != (prof["height"], prof["width"]) or src.transform != prof["transform"]:
                        sys.exit(f"{tifs[m].name} ขนาด/ตำแหน่งไม่ตรงกับเดือนอื่น")
                    a = src.read(1).astype("float32")
                a[(np.abs(a) >= 0.9999) | (a == 0)] = np.nan       # ค่าเพี้ยนแบบเดียวกับ 03_stack_ndvi.py
            else:
                a = np.full((prof["height"], prof["width"]), np.nan, "float32")
            dst.write(a, i)
            dst.set_band_description(i, m)
    to_cog(tmp, out_tif, resampling="average")
    return len(have)


def to_cog(src_path, dst_path, resampling):
    """แปลงเป็น Cloud-Optimized GeoTIFF (มีภาพย่อหลายระดับ ให้ TiTiler อ่านเฉพาะส่วนที่ต้องใช้)"""
    rio_copy(str(src_path), str(dst_path), driver="COG", compress="DEFLATE",
             resampling=resampling, overview_resampling=resampling, blocksize=512, BIGTIFF="IF_SAFER")
    Path(src_path).unlink()


# ---------------------------------------------------------------- 2) ทำนาย
def predict(stack_tif, crop_tif, conf_tif, bundle):
    model, code = bundle["model"], {str(k): int(v) for k, v in bundle["code"].items()}
    classes = [str(c) for c in model.classes_]
    lut = np.array([code[c] for c in classes], dtype="uint8")
    with rasterio.open(stack_tif) as src:
        if src.count != len(bundle["months_order"]):
            sys.exit(f"stack มี {src.count} แบนด์ แต่โมเดลต้องการ {len(bundle['months_order'])}")
        prof = src.profile.copy()
        prof.update(driver="GTiff", count=1, dtype="uint8", nodata=0, compress="deflate", predictor=1,
                    tiled=True, blockxsize=512, blockysize=512)
        tmp_c, tmp_p = crop_tif.with_suffix(".tmp.tif"), conf_tif.with_suffix(".tmp.tif")
        with rasterio.open(tmp_c, "w", **prof) as dmap, rasterio.open(tmp_p, "w", **prof) as dconf:
            dmap.write_colormap(1, {code[c]: (*COLORS.get(c, DEFAULT_COLOR), 255) for c in classes})
            H, W, step = src.height, src.width, 1024
            for r, row in enumerate(range(0, H, step)):
                win = Window(0, row, W, min(step, H - row))
                a = src.read(window=win).astype("float32")
                h, w = a.shape[1:]
                flat = a.reshape(a.shape[0], -1).T
                has = ~np.isnan(flat).all(axis=1)
                out_c = np.zeros(h * w, "uint8")
                out_p = np.zeros(h * w, "uint8")
                if has.any():
                    proba = model.predict_proba(fill_gaps(flat[has]))
                    out_c[has] = lut[proba.argmax(1)]
                    out_p[has] = np.clip(proba.max(1) * 100, 1, 100).astype("uint8")
                dmap.write(out_c.reshape(h, w), 1, window=win)
                dconf.write(out_p.reshape(h, w), 1, window=win)
                print(f"\rทำนาย {r + 1}/{int(np.ceil(H / step))}", end="", flush=True)
    print()
    to_cog(tmp_c, crop_tif, resampling="nearest")      # รหัสคลาสห้ามเฉลี่ย
    to_cog(tmp_p, conf_tif, resampling="nearest")
    return {str(v): k for k, v in code.items()}


# ---------------------------------------------------------------- 3) สรุปพื้นที่
def area_stats(crop_tif, conf_tif, aoi, class_codes):
    with rasterio.open(crop_tif) as s:
        crop, transform, crs = s.read(1), s.transform, s.crs
        bounds = list(transform_bounds(crs, "EPSG:4326", *s.bounds))
    with rasterio.open(conf_tif) as s:
        conf = s.read(1)
    names = list(aoi["adm2_name1"])
    dist = rasterize(((g, i + 1) for i, g in enumerate(aoi.to_crs(crs).geometry)),
                     out_shape=crop.shape, transform=transform, fill=0, dtype="uint8")
    px_m2 = abs(transform.a * transform.e)
    key = dist.astype("uint16") * 256 + crop
    n = (len(names) + 1) * 256
    counts = np.bincount(key.ravel(), minlength=n)
    conf_sum = np.bincount(key.ravel(), weights=conf.ravel().astype("float64"), minlength=n)
    rows = []
    for di, dname in enumerate(names, 1):
        for code, cname in class_codes.items():
            k = di * 256 + int(code)
            c = int(counts[k])
            rows.append({"district": dname, "class": cname, "pixel_count": c,
                         "area_km2": c * px_m2 / 1e6,
                         "mean_conf": float(conf_sum[k] / c) if c else None})
    return rows, bounds, px_m2


def model_scores():
    if not MODEL_REPORT.exists():
        return None, None
    m = re.search(r"Macro-F1 = ([\d.]+)\s+Overall accuracy = ([\d.]+)", MODEL_REPORT.read_text(encoding="utf-8"))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="ทำนายชนิดพืชทั้งฤดูกาล")
    ap.add_argument("--season", type=int, required=True, help="ปีเริ่มฤดูกาล (ค.ศ.) เช่น 2024 = พ.ค. 2024 – เม.ย. 2025")
    ap.add_argument("--stack", type=Path, help="ใช้ NDVI stack 12 แบนด์ที่มีอยู่แล้ว (ข้ามการดึงภาพ)")
    ap.add_argument("--model", type=Path, default=MODEL_FILE)
    ap.add_argument("--model-version", default=None, help="ชื่อเวอร์ชันโมเดล (ค่าเริ่มต้น = วันที่ของไฟล์โมเดล)")
    ap.add_argument("--no-db", action="store_true", help="ไม่บันทึกลงฐานข้อมูล")
    args = ap.parse_args()

    year = args.season
    end = season_months(year)[-1]
    if end.end_time > pd.Timestamp.now():
        sys.exit(f"ฤดูกาล {season_label(year)} ยังไม่จบ (จบ {end}) — โมเดลต้องใช้ข้อมูลครบ 12 เดือน")

    out = SEASONS_DIR / str(year)
    out.mkdir(parents=True, exist_ok=True)
    aoi = gpd.read_file(AOI_FILE).to_crs("EPSG:4326")
    stack = out / "ndvi_stack.tif"

    print(f"=== ฤดูกาล {season_label(year)}: {season_months(year)[0]} ถึง {end} ===")
    if args.stack:
        with rasterio.open(args.stack) as s:
            n_data = sum(1 for i in range(1, s.count + 1) if np.isfinite(s.read(i, out_shape=(64, 64))).any())
        tmp = stack.with_suffix(".tmp.tif")
        shutil.copy(args.stack, tmp)
        to_cog(tmp, stack, resampling="average")
        print(f"ใช้ {args.stack} ({n_data} เดือนมีข้อมูล)")
    else:
        download_months(year, out / "monthly", aoi)
        n_data = build_stack(year, out / "monthly", stack)
        print(f"รวม stack แล้ว: {n_data}/{SEASON_MONTHS} เดือนมีภาพ")

    bundle = joblib.load(args.model)
    class_codes = predict(stack, out / "crop_map.tif", out / "confidence.tif", bundle)
    stats, bounds, px_m2 = area_stats(out / "crop_map.tif", out / "confidence.tif", aoi, class_codes)

    macro, acc = model_scores()
    version = args.model_version or time.strftime("%Y-%m-%d", time.localtime(args.model.stat().st_mtime))
    rel = lambda p: p.relative_to(BASE / "output").as_posix()        # path สัมพัทธ์ ใช้ได้ทั้งบนเครื่องและใน container
    months = season_months(year)
    run = {"season": year, "season_label": season_label(year),
           "month_start": months[0].start_time.date(), "month_end": months[-1].start_time.date(),
           "months_with_data": n_data, "model_name": "RandomForest", "model_version": version,
           "model_macro_f1": macro, "model_accuracy": acc, "class_codes": class_codes,
           "crop_map_path": rel(out / "crop_map.tif"), "confidence_path": rel(out / "confidence.tif"),
           "ndvi_path": rel(stack), "bounds": bounds, "pixel_area_m2": px_m2}

    summary = {**{k: str(v) for k, v in run.items()}, "stats": stats}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    tot = pd.DataFrame(stats).groupby("class")["area_km2"].sum().sort_values(ascending=False)
    print("\nพื้นที่รวม 4 อำเภอ (ตร.กม., นับพิกเซล):\n" + tot.round(1).to_string())

    if args.no_db:
        print("\n--no-db: ไม่บันทึกลงฐานข้อมูล")
        return
    from cropai import db
    try:
        conn = db.connect()
    except Exception as e:
        sys.exit(f"\nต่อฐานข้อมูลไม่ได้ ({e})\nเปิดด้วย: docker compose up -d db  แล้วรันใหม่ หรือใช้ --no-db")
    with conn:
        dist_ids = db.upsert_districts(conn, aoi)
        cls_ids = db.upsert_classes(conn, sorted(set(class_codes.values())))
        rows = [{**s, "district_id": dist_ids[s["district"]], "class_id": cls_ids[s["class"]]} for s in stats]
        run_id = db.save_run(conn, run, rows)
    print(f"\nบันทึกลงฐานข้อมูลแล้ว (map_runs.id = {run_id})")


if __name__ == "__main__":
    main()
