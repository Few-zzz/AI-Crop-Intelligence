# AI Crop Intelligence — จำแนกชนิดพืชจากภาพดาวเทียม

โปรเจกต์สหกิจ: วางแผนและทำระบบจำแนกชนิดพืช 4 เดือน ด้วยเครื่องมือ open-source / self-hosted เท่านั้น (ห้ามใช้ SaaS ราคาแพง) แล้วนำเสนอพี่ที่ดูแล
ตอบและอธิบายเป็นภาษาไทย ผู้ใช้เป็นนักศึกษา CPE ชอบให้อธิบายเหตุผลของแต่ละขั้น
Repo: https://github.com/Few-zzz/AI-Crop-Intelligence (public — ห้าม commit `.env` หรือรหัสผ่าน)

## พื้นที่และข้อมูล
- นครราชสีมา 4 อำเภอ: ด่านขุนทด, สีคิ้ว, พิมาย (เทรน) และ ครบุรี (ทดสอบ แยกพื้นที่ = spatial split)
- `data/aoi.geojson` ขอบเขต 4 อำเภอ (ฟิลด์ชื่ออำเภอ `adm2_name1`, จังหวัด `adm1_name1`)
- `data/label_points.gpkg` จุด label 400 จุด ทำใน QGIS ฟิลด์ `crop`, `note`
  - คลาส: rice, sugarcane, cassava, maize, forest, orchard, water, urban, other, unsure (unsure ถูกตัดทิ้งตอนเทรน)
- ภาพ: Sentinel-2 L2A จาก Earth Search STAC (`https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a`)
- NDVI รายเดือน median composite, 20 ม., EPSG:32647, mask เมฆด้วย SCL [4,5,6,7]
- ข้อมูลเทรน = ฤดูกาล 2568/69 (พ.ค. 2025 – เม.ย. 2026)

## สคริปต์ฝั่งโมเดล (รันตามลำดับ)
| ไฟล์ | หน้าที่ | ผลลัพธ์ |
|---|---|---|
| `00_check_offset.py` | ตรวจว่าข้อมูลมี BOA offset ไหม | พิมพ์ค่าใน console |
| `02_s2_ndvi_monthly.py` | ดึง NDVI 12 เดือน (ข้ามเดือนที่มีแล้ว) | `output/monthly/ndvi_YYYY-MM.tif` |
| `03_stack_ndvi.py` | รวม 12 เดือนเป็นไฟล์เดียว | `output/ndvi_stack_12m.tif` |
| `04_point_curves.py` | ดึงค่า NDVI ที่จุด label + กราฟ | `output/label_points_ndvi.csv`, `output/curves/` |
| `05_train_rf.py` | เทรน Random Forest + วัดผล + ทำแผนที่ | `output/model/` (report.txt, confusion_matrix.png, feature_importance.png, classes.csv, rf_model.joblib, crop_map.tif, confidence.tif) |
| `06_web_app.py` + `web/index.html` | เว็บต้นแบบ (อ่านไฟล์ตรง ไม่ใช้ DB) ดูได้ทีละฤดูกาล | http://127.0.0.1:8000 |
(`01_s2_ndvi.py` เป็นตัวทดลองเดือนเดียว เลิกใช้แล้ว)

## ระบบที่ deploy (ทำนายรายฤดูกาล + เว็บ)
| ส่วน | ไฟล์ | หมายเหตุ |
|---|---|---|
| ทำนายทั้งฤดูกาล | `07_predict.py --season <ปีเริ่ม ค.ศ.>` | `--season 2024` = พ.ค. 2024–เม.ย. 2025 (2567/68), `--stack` ใช้ NDVI ที่มีแล้ว, `--no-db` ไม่ลง DB ผลอยู่ `output/seasons/<ปี>/` (COG) |
| โค้ดกลาง | `cropai/` (config, s2, features, db) | ค่าคงที่ต้องตรงกับ 02/05 |
| ฐานข้อมูล | `db/init/01_schema.sql` | PostGIS 4 ตาราง: districts, crop_classes, map_runs, area_stats (ไม่มี label/users) |
| หลังบ้าน | `api/main.py` (FastAPI) | /api/seasons, /api/runs/{id}/stats, /api/compare, /api/trend, /api/point, /api/change/{a}/{b}/{z}/{x}/{y}.png |
| tile แผนที่ | TiTiler 2.4.0 (container) | อ่าน COG ที่ `/data/seasons/...` |
| หน้าบ้าน | `frontend/` (Next.js 15 + MapLibre 4) | 3 โหมด: ดูรายฤดูกาล / เทียบ 2 ฤดูกาล (เลื่อนเส้นแบ่ง + พิกเซลที่เปลี่ยน) / แนวโน้ม |
| รวมระบบ | `docker-compose.yml` + `.env` (คัดลอกจาก `.env.example`) | `docker compose up -d --build` → http://localhost:3000 |

ดูตารางฐานข้อมูล: `docker compose --profile tools up -d pgadmin` → http://localhost:5050 (โหมดไม่ต้องล็อกอิน, เปิดเฉพาะ 127.0.0.1, ลงทะเบียน server จาก `db/pgadmin/servers.json`; รหัสผ่าน DB อยู่ใน `.env`)
ตอนพัฒนาบนเครื่อง: `docker compose up -d db titiler` แล้วรัน API (`uvicorn api.main:app --port 8001`) กับ `npm --prefix frontend run dev` (มีใน `.claude/launch.json`)

ข้อตกลงการออกแบบ: ไม่มีระบบ login, เว็บดูอย่างเดียว, ทำนายแบบทั้งฤดูกาล (พ.ค.–เม.ย.) เท่านั้น
ยังไม่ได้ตัดสินใจ: ใคร/อะไรสั่งทำนายฤดูกาลใหม่ (คำสั่งมือ หรือ Prefect/cron), เซิร์ฟเวอร์ที่จะ deploy

## เรื่องที่ต้องจำ (เคยพังมาแล้ว)
- **OFFSET = 0** ข้อมูล Earth Search ไม่ได้บวก +1000 แม้ metadata จะบอก offset −0.1 ใช้ `SCALE=0.0001, OFFSET=0.0` ถ้าใส่ offset NDVI จะเพี้ยน (เคยได้ค่า ±1 และพิกเซลหายเกือบหมด)
- STAC search ใช้ `bbox` ไม่ใช่ `intersects` (polygon อำเภอซับซ้อน ทำให้ request ใหญ่เกิน)
- ต้อง `.venv\Scripts\activate` ทุกครั้งก่อนรัน ไม่งั้นจะเจอ ModuleNotFoundError
- ชุดเทรนขยายจุดเป็นหน้าต่าง 3×3 (เก็บเฉพาะพิกเซลที่ RMSE ≤ 0.08 เทียบกับจุดกลาง) ชุดทดสอบใช้พิกเซลเดียว ห้ามให้พิกเซลแปลงเดียวกันอยู่ทั้งสองชุด
- เติมช่องว่างเดือนที่มีเมฆด้วยค่าเฉลี่ย ffill/bfill ตามแกนเดือน (`fill_gaps`) — ต้องใช้วิธีเดียวกันตอนทำนาย
- ฟอนต์ไทยใน matplotlib: `["Tahoma", "Leelawadee UI", "DejaVu Sans"]`
- เคยพิมพ์ผิด "umsure" ใน label จนกลายเป็นคลาสใหม่ ต้องแก้เป็น "unsure" ใน QGIS (โมเดลรอบ 2 ยังมีคลาส umsure)
- ต่อ DB บน Windows ใช้ `127.0.0.1` ไม่ใช่ `localhost` (localhost ลอง IPv6 ก่อนแล้วค้าง)
- ห้ามใช้ `Get-Content -Raw | Set-Content` ของ PowerShell 5.1 แก้ไฟล์ที่มีภาษาไทย (อ่าน UTF-8 ผิดเป็น ANSI ไฟล์เพี้ยน)
- แผนที่ MapLibre ใน Next.js: `.maplibregl-map` ถูกตั้ง `position: relative` ต้องเขียน CSS เจาะจงกว่า และใช้ ResizeObserver เรียก `resize()` ไม่งั้นแผนที่สูง 0
- rewrites ของ Next.js อ่าน `API_URL`/`TITILER_URL` ตอน build (Dockerfile ตั้งเป็นชื่อ service ของ compose)

## โมเดลปัจจุบัน
- RandomForestClassifier(n_estimators=300, class_weight="balanced", min_samples_leaf=2, n_jobs=-1, random_state=42)
- Feature: NDVI 12 เดือน, ตัวชี้วัดหลัก Macro-F1 บนชุดทดสอบครบุรี
- รอบ 1: Macro-F1 0.43 (ป่าในชุดเทรนน้อยเกิน) → เพิ่ม label ป่า/ชุมชน
- รอบ 2: Macro-F1 0.64, Accuracy 0.83 — forest 0.99, water 1.00, urban 0.75, rice 0.67, cassava 0.65, sugarcane 0.43, maize 0.00
- คอขวด: มันสำปะหลัง vs อ้อย, ข้าวโพดมีตัวอย่างน้อย (7 จุด)
- เดือนสำคัญจาก feature importance: ธ.ค. แล้ว พ.ค.
- ความแม่นในฤดูกาลอื่นยังไม่ได้วัด (พืชหมุนเวียน/สภาพอากาศต่าง) — `map_runs.season_macro_f1` ไว้ใส่เมื่อมีชุดตรวจของปีนั้น

## งานที่ค้าง / ต่อไป
ฝั่งโมเดล (ยังไม่แตะ):
1. แก้ "umsure" → "unsure", ย้าย maize ไป other หรือ label ให้ได้ ~20 จุด แล้วรัน 05 ใหม่ (ผู้ใช้ทำใน QGIS)
2. ดึง 6 แบนด์ (B,G,R,NIR,SWIR1,SWIR2) แทน NDVI อย่างเดียว → LightGBM, TempCNN (PyTorch, CUDA 12.8 บน RTX 5060 Ti)
3. ประมาณพื้นที่พร้อมช่วงความเชื่อมั่นตามแนว Olofsson et al. 2014 (สุ่มจุดตรวจแบบแบ่งชั้น → ใส่ `area_stats.adj_area_km2`, `ci95_km2`)
ฝั่งระบบ:
4. ตัดสินใจวิธีสั่งทำนายฤดูกาลใหม่ และเซิร์ฟเวอร์ที่จะ deploy
5. ฤดูกาล 2569/70 ทำนายได้หลังจบ เม.ย. 2027
(ตัด Prithvi-EO-2.0 / TerraTorch ออกจากแผนแล้ว)

## รายงานสหกิจ
- ไฟล์หลักของผู้ใช้: `Downloads\66070842_รายงานเตรียมสหกิจ.docx` (ผู้ใช้แก้เอง เลือกรับการแก้บางส่วน) ฟอนต์ TH SarabunPSK
- บทที่ 2 ที่ทำให้: `Downloads\66070842_รายงานเตรียมสหกิจ_บทที่2.docx` (งานวิจัย 9 งานใน 2.3 ยืนยัน DOI กับ Crossref แล้ว, บรรณานุกรมท้ายเล่ม)
- แก้ docx ด้วยการแก้ XML โดยตรง แล้วเปิดด้วย Word (COM) เพื่ออัปเดตสารบัญและ export PDF ตรวจหน้าตา; ใส่ zero-width space ระหว่างคำไทยให้ Word ตัดบรรทัดถูก
- บทที่ 3 ยังไม่ได้ทำ (Use Case: ผู้ใช้ทั่วไป / ผู้ดูแลระบบ ไม่มี login, ER Diagram 4 ตาราง)

## เครื่องมือ (open-source ทั้งหมด)
QGIS, Python (pystac-client, odc-stac, rasterio, geopandas, xarray, scikit-learn), LightGBM, PyTorch, Optuna, MLflow, FastAPI, PostgreSQL/PostGIS, TiTiler, Next.js, MapLibre, Docker, Prefect (แผน)
