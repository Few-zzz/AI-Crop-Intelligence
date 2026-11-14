-- ฐานข้อมูลของระบบที่ deploy: เก็บเฉพาะสิ่งที่เว็บใช้แสดงผล (4 ตาราง)
-- ไฟล์นี้รันอัตโนมัติครั้งแรกที่สร้าง container ฐานข้อมูล (docker-entrypoint-initdb.d)
CREATE EXTENSION IF NOT EXISTS postgis;

-- ขอบเขตอำเภอ (บทบาท train/test ตาม spatial split ของโมเดล)
CREATE TABLE IF NOT EXISTS districts (
    id       SERIAL PRIMARY KEY,
    name_th  TEXT NOT NULL UNIQUE,
    province TEXT NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('train', 'test')),
    geom     geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS districts_geom_idx ON districts USING GIST (geom);

-- รายชื่อคลาส (ชื่ออังกฤษเป็นตัวหลัก กันพิมพ์ผิดแล้วกลายเป็นคลาสใหม่)
CREATE TABLE IF NOT EXISTS crop_classes (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    name_th TEXT NOT NULL,
    color   TEXT NOT NULL          -- #rrggbb
);

-- การทำนายแต่ละฤดูกาล (หนึ่งแถว = หนึ่งแผนที่)
CREATE TABLE IF NOT EXISTS map_runs (
    id                SERIAL PRIMARY KEY,
    season            INT  NOT NULL,          -- ปีเริ่ม (ค.ศ.) เช่น 2025 = พ.ค. 2025 – เม.ย. 2026
    season_label      TEXT NOT NULL,          -- เช่น 2568/69
    month_start       DATE NOT NULL,
    month_end         DATE NOT NULL,
    months_with_data  INT  NOT NULL,          -- เดือนที่มีภาพจริง (ที่เหลือเติมช่องว่าง)
    model_name        TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    model_macro_f1    DOUBLE PRECISION,       -- คะแนนของโมเดลบนชุดทดสอบตอนเทรน (ไม่ใช่ของฤดูกาลนี้)
    model_accuracy    DOUBLE PRECISION,
    season_macro_f1   DOUBLE PRECISION,       -- คะแนนของฤดูกาลนี้ ถ้ามีชุดตรวจของปีนั้น
    class_codes       JSONB NOT NULL,         -- รหัสในไฟล์แผนที่ -> ชื่อคลาส เช่น {"1": "cassava"}
    crop_map_path     TEXT NOT NULL,          -- path สัมพัทธ์จากโฟลเดอร์ข้อมูล เช่น seasons/2025/crop_map.tif
    confidence_path   TEXT NOT NULL,
    ndvi_path         TEXT NOT NULL,
    bounds            DOUBLE PRECISION[] NOT NULL,   -- [west, south, east, north] (lon/lat)
    pixel_area_m2     DOUBLE PRECISION NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season, model_name, model_version)
);

-- พื้นที่แต่ละคลาสในแต่ละอำเภอของแต่ละฤดูกาล
CREATE TABLE IF NOT EXISTS area_stats (
    run_id       INT NOT NULL REFERENCES map_runs(id) ON DELETE CASCADE,
    district_id  INT NOT NULL REFERENCES districts(id),
    class_id     INT NOT NULL REFERENCES crop_classes(id),
    pixel_count  BIGINT NOT NULL,
    area_km2     DOUBLE PRECISION NOT NULL,   -- นับพิกเซล × พื้นที่พิกเซล
    adj_area_km2 DOUBLE PRECISION,            -- ปรับตาม Olofsson et al. (2014) เมื่อมีจุดตรวจสอบ
    ci95_km2     DOUBLE PRECISION,
    mean_conf    DOUBLE PRECISION,            -- ความมั่นใจเฉลี่ยของพิกเซลคลาสนี้
    PRIMARY KEY (run_id, district_id, class_id)
);
