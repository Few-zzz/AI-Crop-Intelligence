# AI Crop Intelligence — จำแนกชนิดพืชจากภาพดาวเทียม

โปรเจกต์สหกิจ: วางแผนและทำระบบจำแนกชนิดพืช 4 เดือน ด้วยเครื่องมือ open-source / self-hosted เท่านั้น (ห้ามใช้ SaaS ราคาแพง) แล้วนำเสนอพี่ที่ดูแล
ตอบและอธิบายเป็นภาษาไทย ผู้ใช้เป็นนักศึกษา CPE ชอบให้อธิบายเหตุผลของแต่ละขั้น

## พื้นที่และข้อมูล
- นครราชสีมา 4 อำเภอ: ด่านขุนทด, สีคิ้ว, พิมาย (เทรน) และ ครบุรี (ทดสอบ แยกพื้นที่ = spatial split)
- `data/aoi.geojson` ขอบเขต 4 อำเภอ (ฟิลด์ชื่ออำเภอ `adm2_name1`, จังหวัด `adm1_name1`)
- `data/label_points.gpkg` จุด label ~400 จุด ทำใน QGIS ฟิลด์ `crop`, `note`
  - คลาส: rice, sugarcane, cassava, maize, forest, orchard, water, urban, other, unsure (unsure ถูกตัดทิ้งตอนเทรน)
- ภาพ: Sentinel-2 L2A จาก Earth Search STAC (`https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a`)
- NDVI รายเดือน พ.ค. 2025 – เม.ย. 2026, median composite, 20 ม., EPSG:32647, mask เมฆด้วย SCL [4,5,6,7]

## สคริปต์ (รันตามลำดับ)
| ไฟล์ | หน้าที่ | ผลลัพธ์ |
|---|---|---|
| `00_check_offset.py` | ตรวจว่าข้อมูลมี BOA offset ไหม | พิมพ์ค่าใน console |
| `02_s2_ndvi_monthly.py` | ดึง NDVI 12 เดือน (ข้ามเดือนที่มีแล้ว) | `output/monthly/ndvi_YYYY-MM.tif` |
| `03_stack_ndvi.py` | รวม 12 เดือนเป็นไฟล์เดียว | `output/ndvi_stack_12m.tif` |
| `04_point_curves.py` | ดึงค่า NDVI ที่จุด label + กราฟ | `output/label_points_ndvi.csv`, `output/curves/` |
| `05_train_rf.py` | เทรน Random Forest + วัดผล + ทำแผนที่ | `output/model/` (report.txt, confusion_matrix.png, feature_importance.png, classes.csv, rf_model.joblib, crop_map.tif, confidence.tif) |
| `06_web_app.py` + `web/index.html` | เว็บ FastAPI + MapLibre ดูผล | http://127.0.0.1:8000 |
(`01_s2_ndvi.py` เป็นตัวทดลองเดือนเดียว เลิกใช้แล้ว)

## เรื่องที่ต้องจำ (เคยพังมาแล้ว)
- **OFFSET = 0** ข้อมูล Earth Search ไม่ได้บวก +1000 แม้ metadata จะบอก offset −0.1 ใช้ `SCALE=0.0001, OFFSET=0.0` ถ้าใส่ offset NDVI จะเพี้ยน (เคยได้ค่า ±1 และพิกเซลหายเกือบหมด)
- STAC search ใช้ `bbox` ไม่ใช่ `intersects` (polygon อำเภอซับซ้อน ทำให้ request ใหญ่เกิน)
- ต้อง `.venv\Scripts\activate` ทุกครั้งก่อนรัน ไม่งั้นจะเจอ ModuleNotFoundError
- ชุดเทรนขยายจุดเป็นหน้าต่าง 3×3 (เก็บเฉพาะพิกเซลที่ RMSE ≤ 0.08 เทียบกับจุดกลาง) ชุดทดสอบใช้พิกเซลเดียว ห้ามให้พิกเซลแปลงเดียวกันอยู่ทั้งสองชุด
- เติมช่องว่างเดือนที่มีเมฆด้วยค่าเฉลี่ย ffill/bfill ตามแกนเดือน (`fill_gaps`)
- ฟอนต์ไทยใน matplotlib: `["Tahoma", "Leelawadee UI", "DejaVu Sans"]`
- เคยพิมพ์ผิด "umsure" ใน label จนกลายเป็นคลาสใหม่ ต้องแก้เป็น "unsure" ใน QGIS

## โมเดลปัจจุบัน
- RandomForestClassifier(n_estimators=300, class_weight="balanced", min_samples_leaf=2, n_jobs=-1, random_state=42)
- Feature: NDVI 12 เดือน, ตัวชี้วัดหลัก Macro-F1 บนชุดทดสอบครบุรี
- รอบ 1: Macro-F1 0.43 (ป่าในชุดเทรนน้อยเกิน) → เพิ่ม label ป่า/ชุมชน
- รอบ 2: Macro-F1 0.64, Accuracy 0.83 — forest 0.99, water 1.00, urban 0.75, rice 0.67, cassava 0.65, sugarcane 0.43, maize 0.00
- คอขวด: มันสำปะหลัง vs อ้อย (งาน GISTAM 2019 ที่โคราชก็เจอ), ข้าวโพดมีตัวอย่างน้อย
- เดือนสำคัญจาก feature importance: ธ.ค. แล้ว พ.ค.

## งานที่ค้าง / ต่อไป
1. แก้ "umsure" → "unsure", ย้าย maize ไป other หรือ label ให้ได้ ~20 จุด แล้วรัน 05 ใหม่
2. `07_predict.py` ใช้ rf_model.joblib ทำนายพื้นที่/ปีใหม่
3. ดึง 6 แบนด์ (B,G,R,NIR,SWIR1,SWIR2) แทน NDVI อย่างเดียว → LightGBM, TempCNN (PyTorch)
4. ทดลอง Prithvi-EO-2.0 300M ผ่าน TerraTorch บน RTX 5060 Ti (ต้อง PyTorch cu128) เทียบกับ RF บนชุดครบุรีชุดเดิม
5. รายงานช่วงความเชื่อมั่นและประมาณพื้นที่ตามแนว Olofsson et al. 2014
6. (ถ้าต่อยอด) เว็บเป็น Next.js หน้าบ้าน + FastAPI หลังบ้าน, Docker Compose

## เครื่องมือในแผน (open-source ทั้งหมด)
QGIS, Python (pystac-client, odc-stac, rasterio, geopandas, xarray, scikit-learn), LightGBM, PyTorch, TerraTorch, Optuna, MLflow, FastAPI, PostGIS, TiTiler/GeoServer, MapLibre, Prefect, Docker
