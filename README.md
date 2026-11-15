# AI Crop Intelligence

จำแนกชนิดพืชจากอนุกรมเวลา NDVI ของภาพดาวเทียม Sentinel-2 ในจังหวัดนครราชสีมา ด้วยเครื่องมือ open-source ทั้งหมด

- **พื้นที่:** 4 อำเภอ ได้แก่ ด่านขุนทด สีคิ้ว พิมาย (ชุดเทรน) และครบุรี (ชุดทดสอบ แยกพื้นที่กันแบบ spatial split)
- **ภาพ:** Sentinel-2 L2A จาก [Earth Search STAC](https://earth-search.aws.element84.com/v1) ทำ median composite รายเดือน พ.ค. 2025 – เม.ย. 2026 ที่ 20 ม. (EPSG:32647) และตัดเมฆด้วย SCL
- **Label:** จุดตัวอย่าง 400 จุด ทำเองใน QGIS
- **โมเดล:** Random Forest ใช้ NDVI 12 เดือนเป็น feature
- **คลาส:** ข้าว, อ้อย, มันสำปะหลัง, ข้าวโพด, ป่าไม้, แหล่งน้ำ, ชุมชน

## ผลลัพธ์ปัจจุบัน (รอบ 2)

วัดผลบนพื้นที่ครบุรี ซึ่งโมเดลไม่เคยเห็นตอนเทรน:

| ตัวชี้วัด | ค่า |
|---|---|
| Macro-F1 | 0.64 |
| Overall accuracy | 0.83 |

ป่าไม้และแหล่งน้ำจำแนกได้แม่น (F1 ≈ 0.99–1.00) คอขวดอยู่ที่การแยก **มันสำปะหลังกับอ้อย** ซึ่งมีช่วงการเติบโตใกล้กัน ส่วนข้าวโพดมีตัวอย่างน้อยเกินไป เดือนที่โมเดลให้น้ำหนักมากที่สุดคือ ธ.ค. และ พ.ค.

| Confusion matrix | Feature importance |
|---|---|
| ![confusion matrix](output/model/confusion_matrix.png) | ![feature importance](output/model/feature_importance.png) |

รายละเอียดเต็มอยู่ใน [output/model/report.txt](output/model/report.txt)

## Pipeline

| ไฟล์ | หน้าที่ | ผลลัพธ์ |
|---|---|---|
| `00_check_offset.py` | ตรวจว่าข้อมูลมี BOA offset หรือไม่ | พิมพ์ค่าใน console |
| `02_s2_ndvi_monthly.py` | ดึง NDVI รายเดือน 12 เดือน (ข้ามเดือนที่มีไฟล์แล้ว) | `output/monthly/ndvi_YYYY-MM.tif` |
| `03_stack_ndvi.py` | รวม 12 เดือนเป็นไฟล์เดียว | `output/ndvi_stack_12m.tif` |
| `04_point_curves.py` | ดึงค่า NDVI ที่จุด label และวาดกราฟ | `output/label_points_ndvi.csv`, `output/curves/` |
| `05_train_rf.py` | เทรน Random Forest วัดผล และทำแผนที่ | `output/model/` |
| `06_web_app.py` + `web/index.html` | เว็บดูผล (FastAPI + MapLibre) | http://127.0.0.1:8000 |

`01_s2_ndvi.py` เป็นตัวทดลองเดือนเดียว เลิกใช้แล้ว

## วิธีรัน

ต้องมี Python 3.12 และใช้ Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python 02_s2_ndvi_monthly.py   # ใช้เวลานาน ดึงภาพจากอินเทอร์เน็ต
python 03_stack_ndvi.py
python 04_point_curves.py
python 05_train_rf.py
python 06_web_app.py           # เปิด http://127.0.0.1:8000
```

ถ้า PowerShell ไม่ยอมให้ activate ให้รัน `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` ก่อน

## ระบบที่ใช้งานจริง (ทำนายรายฤดูกาล + เว็บ)

```
07_predict.py ──> output/seasons/<ปี>/*.tif (COG) ──> TiTiler ──┐
       └──────> PostgreSQL + PostGIS ──> FastAPI (api/) ──────┴──> Next.js + MapLibre (frontend/)
```

| ส่วน | ที่อยู่ | หน้าที่ |
|---|---|---|
| ทำนาย | `07_predict.py` | ดึงภาพทั้งฤดูกาล (พ.ค.–เม.ย.) → ทำนายด้วยโมเดลที่เทรนแล้ว → บันทึกไฟล์และฐานข้อมูล |
| ฐานข้อมูล | `db/init/01_schema.sql` | 4 ตาราง: `districts`, `crop_classes`, `map_runs`, `area_stats` |
| หลังบ้าน | `api/` | FastAPI: รายการฤดูกาล, สถิติพื้นที่, เทียบ 2 ฤดูกาล, แนวโน้ม, คลิกดูพิกเซล |
| tile แผนที่ | TiTiler | ตัด tile จากไฟล์ COG |
| หน้าบ้าน | `frontend/` | ดูรายฤดูกาล / เทียบ 2 ฤดูกาล (เลื่อนเส้นแบ่ง + พิกเซลที่เปลี่ยนชนิด) / กราฟแนวโน้ม |

### เริ่มใช้งาน

```powershell
copy .env.example .env          # แล้วแก้ POSTGRES_PASSWORD ในไฟล์ .env
docker compose up -d db          # เปิดฐานข้อมูล

# ทำนายฤดูกาล (ปีเริ่มเป็น ค.ศ.)
python 07_predict.py --season 2025 --stack output/ndvi_stack_12m.tif   # ใช้ NDVI ที่มีอยู่แล้ว
python 07_predict.py --season 2024                                     # ดึงภาพใหม่ ใช้เวลาราว 1 ชั่วโมง

docker compose up -d --build     # เปิดทั้งระบบ แล้วเข้า http://localhost:3000
```

### Deploy ขึ้นเซิร์ฟเวอร์

1. ติดตั้ง Docker และ Docker Compose บนเซิร์ฟเวอร์ (Linux)
2. `git clone` repo นี้ แล้วสร้าง `.env` จาก `.env.example` (ตั้งรหัสผ่านใหม่)
3. คัดลอกโฟลเดอร์ `output/seasons/` จากเครื่องที่ทำนายไปไว้ที่ตำแหน่งเดียวกัน หรือรัน `07_predict.py` บนเซิร์ฟเวอร์
4. `docker compose up -d --build` แล้วเปิดพอร์ต `WEB_PORT` (ค่าเริ่มต้น 3000) ให้ผู้ใช้เข้าถึง
5. ฐานข้อมูล, TiTiler และ API เปิดพอร์ตเฉพาะ `127.0.0.1` คนนอกเข้าได้แค่หน้าเว็บ ถ้าจะใช้โดเมนและ HTTPS ให้วาง reverse proxy (เช่น Nginx หรือ Caddy) หน้าพอร์ต 3000

### ย้ายโปรเจกต์ไปเครื่องอื่น

โค้ดย้ายผ่าน GitHub ส่วนข้อมูลที่ไม่อยู่ใน Git (ฐานข้อมูล, `output/`, `.env`) ใช้ 2 สคริปต์นี้

```powershell
# เครื่องเดิม: แพ็กลงโฟลเดอร์เดียว (ค่าเริ่มต้น ..\crop-ai-move ราว 2 GB, -Full = เอา output/ ทั้งหมด)
powershell -ExecutionPolicy Bypass -File scripts\pack_for_move.ps1 -Dest E:\crop-ai-move

# เครื่องใหม่ (ต้องมี Git, Python 3.12, Docker Desktop)
git clone https://github.com/Few-zzz/AI-Crop-Intelligence.git crop-ai
cd crop-ai
powershell -ExecutionPolicy Bypass -File scripts\restore_on_new_machine.ps1 -Pack E:\crop-ai-move
```

สคริปต์ restore จะตรวจ checksum, แตก `output/`, สร้าง `.venv`, restore ฐานข้อมูล แล้วเปิดทั้งระบบ ถ้าไม่มีไฟล์ dump ใช้ `-Rebuild` ให้สร้างข้อมูลใหม่ด้วย `07_predict.py --stack` (ไม่ต้องใช้เน็ต)
โฟลเดอร์ที่แพ็กมี `.env` (รหัสผ่าน) อยู่ด้วย ห้ามอัปขึ้นที่สาธารณะ

### เว็บต้นแบบ

`06_web_app.py` + `web/index.html` เป็นเว็บรุ่นแรก อ่านไฟล์ผลลัพธ์ตรง ไม่ใช้ฐานข้อมูล ดูได้ทีละฤดูกาล

พื้นที่ในเว็บทั้งสองแบบมาจากการนับพิกเซล ยังไม่ได้ปรับตามวิธี Olofsson et al. 2014

## ข้อควรระวัง

- **ใช้ `OFFSET = 0`** ข้อมูล Earth Search ไม่ได้บวก +1000 แม้ metadata จะระบุ offset −0.1
- **ค้นหา STAC ด้วย `bbox`** ไม่ใช่ `intersects` เพราะ polygon อำเภอซับซ้อนจน request ใหญ่เกิน
- **กันข้อมูลรั่วระหว่างชุด** ชุดเทรนขยายแต่ละจุดเป็นหน้าต่าง 3×3 (เก็บเฉพาะพิกเซลที่ RMSE ≤ 0.08 เทียบกับจุดกลาง) ส่วนชุดทดสอบใช้พิกเซลเดียว และอยู่คนละอำเภอ

## งานต่อไป

1. ทำ label ข้าวโพดเพิ่ม หรือรวมเข้ากับคลาส other
2. ใช้ 6 แบนด์ (B, G, R, NIR, SWIR1, SWIR2) กับ LightGBM และ TempCNN
3. ประมาณพื้นที่พร้อมช่วงความเชื่อมั่นตามแนว Olofsson et al. 2014
4. วัดความแม่นของโมเดลในฤดูกาลอื่น (label ชุดตรวจของปีนั้น)
5. ตั้งเวลาทำนายฤดูกาลใหม่อัตโนมัติ และเลือกเซิร์ฟเวอร์ที่จะ deploy

## แหล่งข้อมูล

- ภาพ Sentinel-2: Copernicus / ESA ผ่าน Element 84 Earth Search
- ขอบเขตอำเภอ (`data/aoi.geojson`): ชุดขอบเขตการปกครองของประเทศไทย (COD-AB) จาก OCHA / HDX
- แผนที่ฐานในเว็บ: © OpenStreetMap contributors และ Esri World Imagery
