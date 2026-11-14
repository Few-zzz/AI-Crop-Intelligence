"""ค่าตั้งต้นของระบบ: path, ค่าคงที่ของภาพ Sentinel-2, ชื่อคลาส และการเชื่อมต่อฐานข้อมูล"""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
AOI_FILE = BASE / "data" / "aoi.geojson"
MODEL_FILE = BASE / "output" / "model" / "rf_model.joblib"
MODEL_REPORT = BASE / "output" / "model" / "report.txt"
SEASONS_DIR = BASE / "output" / "seasons"     # ผลรายฤดูกาล: output/seasons/<ปีเริ่ม>/...

# ---- Sentinel-2 (ต้องตรงกับ 02_s2_ndvi_monthly.py ที่ใช้ทำข้อมูลเทรน)
STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
MAX_CLOUD = 70
RESOLUTION = 20
CRS = "EPSG:32647"
VALID_SCL = [4, 5, 6, 7]
SCALE, OFFSET = 0.0001, 0.0       # Earth Search ไม่ได้บวก +1000 (ดู 00_check_offset.py)

SEASON_START_MONTH = 5            # ฤดูกาลเพาะปลูก = พ.ค. ปีนี้ ถึง เม.ย. ปีหน้า
SEASON_MONTHS = 12
TEST_DISTRICT = "ครบุรี"

NAME_TH = {"rice": "ข้าว", "sugarcane": "อ้อย", "cassava": "มันสำปะหลัง", "maize": "ข้าวโพด",
           "forest": "ป่าไม้", "orchard": "สวนผลไม้", "water": "แหล่งน้ำ",
           "urban": "ชุมชน/สิ่งปลูกสร้าง", "other": "อื่น ๆ", "unsure": "ไม่แน่ใจ",
           "umsure": "ไม่แน่ใจ (สะกดผิด umsure)"}
COLORS = {"rice": (31, 119, 180), "sugarcane": (44, 160, 44), "cassava": (255, 127, 14),
          "maize": (188, 189, 34), "forest": (23, 100, 60), "orchard": (227, 119, 194),
          "water": (30, 60, 200), "urban": (140, 86, 75), "other": (148, 103, 189)}
DEFAULT_COLOR = (200, 200, 200)


def load_env():
    """อ่านไฟล์ .env ของโปรเจกต์ (ถ้ามี) เข้า os.environ โดยไม่ทับค่าที่ตั้งไว้แล้ว"""
    f = BASE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def database_url():
    load_env()
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    # ใช้ 127.0.0.1 ไม่ใช่ localhost: บน Windows localhost ลอง IPv6 ก่อนแล้วค้างนาน
    return (f"postgresql://{os.environ.get('POSTGRES_USER', 'cropai')}:{os.environ.get('POSTGRES_PASSWORD', '')}"
            f"@{os.environ.get('POSTGRES_HOST', '127.0.0.1')}:{os.environ.get('POSTGRES_PORT', '5432')}"
            f"/{os.environ.get('POSTGRES_DB', 'cropai')}")


def season_label(year: int) -> str:
    """2025 -> '2568/69' (ปีพุทธศักราชแบบที่ใช้ในรายงาน)"""
    be = year + 543
    return f"{be}/{str(be + 1)[-2:]}"
