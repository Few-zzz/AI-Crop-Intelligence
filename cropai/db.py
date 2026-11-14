"""เขียนผลการทำนายลงฐานข้อมูล PostGIS"""
import json

import psycopg

from .config import COLORS, DEFAULT_COLOR, NAME_TH, TEST_DISTRICT, database_url


def connect():
    return psycopg.connect(database_url(), connect_timeout=5)


def upsert_districts(conn, aoi):
    """ขอบเขตอำเภอจาก data/aoi.geojson -> ตาราง districts (คืน {ชื่อ: id})"""
    ids = {}
    with conn.cursor() as cur:
        for _, r in aoi.to_crs("EPSG:4326").iterrows():
            cur.execute(
                """INSERT INTO districts (name_th, province, role, geom)
                   VALUES (%s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)))
                   ON CONFLICT (name_th) DO UPDATE SET province = EXCLUDED.province,
                       role = EXCLUDED.role, geom = EXCLUDED.geom
                   RETURNING id""",
                (r["adm2_name1"], r["adm1_name1"], "test" if r["adm2_name1"] == TEST_DISTRICT else "train",
                 r.geometry.wkt))
            ids[r["adm2_name1"]] = cur.fetchone()[0]
    return ids


def upsert_classes(conn, names):
    ids = {}
    with conn.cursor() as cur:
        for n in names:
            color = "#%02x%02x%02x" % COLORS.get(n, DEFAULT_COLOR)
            cur.execute(
                """INSERT INTO crop_classes (name, name_th, color) VALUES (%s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET name_th = EXCLUDED.name_th, color = EXCLUDED.color
                   RETURNING id""", (n, NAME_TH.get(n, n), color))
            ids[n] = cur.fetchone()[0]
    return ids


def save_run(conn, run: dict, stats: list):
    """บันทึกหนึ่งฤดูกาล ถ้าเคยมีฤดูกาล+โมเดลเดียวกันจะลบของเก่าก่อน (รันซ้ำได้)
    stats: [{district, class, pixel_count, area_km2, mean_conf}]"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM map_runs WHERE season=%s AND model_name=%s AND model_version=%s",
                    (run["season"], run["model_name"], run["model_version"]))
        cols = list(run)
        vals = [json.dumps(v, ensure_ascii=False) if k == "class_codes" else v for k, v in run.items()]
        cur.execute(f"INSERT INTO map_runs ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                    vals)
        run_id = cur.fetchone()[0]
        cur.executemany(
            """INSERT INTO area_stats (run_id, district_id, class_id, pixel_count, area_km2, mean_conf)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            [(run_id, s["district_id"], s["class_id"], s["pixel_count"], s["area_km2"], s["mean_conf"])
             for s in stats])
    conn.commit()
    return run_id
