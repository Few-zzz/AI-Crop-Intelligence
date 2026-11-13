"""
ขั้นที่ 5: เทรน Random Forest (baseline) + วัดผลกับครบุรี + ทำแผนที่ชนิดพืช

ใช้: python 05_train_rf.py
ต้องมี: data/label_points.gpkg (มีคอลัมน์ crop, adm2_name1) และ output/ndvi_stack_12m.tif
ติดตั้งเพิ่มครั้งเดียว: pip install scikit-learn

ผลลัพธ์ (โฟลเดอร์ output/model/):
- (จุดเทรนถูกขยายเป็นพิกเซล 3x3 ที่คล้ายจุดกลาง, จุดทดสอบใช้พิกเซลเดียว)
- report.txt              precision / recall / F1 รายคลาส + macro-F1 ของชุดทดสอบ (ครบุรี)
- confusion_matrix.png    ตารางว่าพืชไหนถูกทายเป็นพืชไหน
- feature_importance.png  เดือนไหนสำคัญต่อการแยกพืช
- crop_map.tif            แผนที่ชนิดพืชทั้ง 4 อำเภอ (เปิดใน QGIS มีสีให้แล้ว)
- confidence.tif          ความมั่นใจของโมเดล 0-100 (จุดที่ต่ำ = ควรไป label เพิ่ม)
- classes.csv             รหัสตัวเลข -> ชื่อคลาส
- rf_model.joblib         ตัวโมเดลที่เทรนแล้ว เอาไปใช้กับข้อมูลใหม่ได้
"""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (ConfusionMatrixDisplay, classification_report,
                             confusion_matrix, f1_score)

plt.rcParams["font.family"] = ["Tahoma", "Leelawadee UI", "DejaVu Sans"]

BASE = Path(__file__).resolve().parent
POINTS = BASE / "data" / "label_points.gpkg"
STACK = BASE / "output" / "ndvi_stack_12m.tif"
OUT = BASE / "output" / "model"

TEST_AMPHOE = "ครบุรี"
IGNORE = {"", "unsure"}
MIN_TRAIN = 5          # คลาสที่มีตัวอย่างเทรนน้อยกว่านี้จะเตือน
SIMILAR_RMSE = 0.08    # พิกเซลรอบจุดที่กราฟ NDVI ต่างจากจุดกลางเกินนี้ ไม่เอามาเทรน
COLORS = {"rice": (31, 119, 180), "sugarcane": (44, 160, 44), "cassava": (255, 127, 14),
          "maize": (188, 189, 34), "forest": (23, 100, 60), "orchard": (227, 119, 194),
          "water": (30, 60, 200), "urban": (140, 86, 75), "other": (148, 103, 189)}


def fill_gaps(X):
    """เติมเดือนที่เมฆบัง (NaN) ด้วยค่าเฉลี่ยของเดือนก่อน-หลังที่มีข้อมูล
    ใช้วิธีเดียวกันทั้งตอนเทรนและตอนทำแผนที่"""
    df = pd.DataFrame(X)
    f, b = df.ffill(axis=1), df.bfill(axis=1)
    out = (f + b) / 2
    out = out.fillna(f).fillna(b)
    return out.to_numpy(dtype="float32")


def expand_points(pts, months):
    """ขยายแต่ละจุดเป็นพิกเซล 3x3 รอบ ๆ แล้วเก็บเฉพาะพิกเซลที่กราฟ NDVI 12 เดือน
    ใกล้เคียงจุดกลาง (RMSE <= SIMILAR_RMSE) - ตัดพิกเซลที่เลยไปแปลงอื่น/คันนา/ต้นไม้ทิ้ง"""
    Xs, ys = [], []
    with rasterio.open(STACK) as src:
        for geom, crop in zip(pts.to_crs(src.crs).geometry, pts["crop"]):
            r, c = src.index(geom.x, geom.y)
            win = Window(c - 1, r - 1, 3, 3)
            a = src.read(window=win, boundless=True, fill_value=np.nan).astype("float32")
            nb = a.reshape(a.shape[0], -1).T                  # (9, 12) - แถวที่ 4 คือจุดกลาง
            has = ~np.isnan(nb).all(axis=1)
            if not has[4]:
                continue
            nb_f = np.full_like(nb, np.nan)
            nb_f[has] = fill_gaps(nb[has])
            center = nb_f[4]
            rmse = np.sqrt(np.nanmean((nb_f - center) ** 2, axis=1))
            keep = has & (rmse <= SIMILAR_RMSE) & ~np.isnan(nb_f).any(axis=1)
            Xs.append(nb_f[keep])
            ys += [crop] * int(keep.sum())
    if not Xs:
        return np.empty((0, len(months)), "float32"), np.array([])
    return np.vstack(Xs), np.array(ys)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # ---------- 1) ตารางข้อมูลเทรน ----------
    pts = gpd.read_file(POINTS, fid_as_index=True)
    pts["crop"] = pts["crop"].fillna("").str.strip().str.lower()
    pts = pts[~pts["crop"].isin(IGNORE)]
    with rasterio.open(STACK) as src:
        months = [d or f"b{i+1}" for i, d in enumerate(src.descriptions)]
        xy = [(g.x, g.y) for g in pts.to_crs(src.crs).geometry]
        X = np.array(list(src.sample(xy)), dtype="float32")
        profile = src.profile.copy()

    X = fill_gaps(X)
    ok = ~np.isnan(X).any(axis=1)          # จุดที่ไม่มีข้อมูลเลยสักเดือน (นอกภาพ) ตัดทิ้ง
    X, pts = X[ok], pts[ok]
    y = pts["crop"].to_numpy()
    is_test = (pts["adm2_name1"] == TEST_AMPHOE).to_numpy()

    print("จำนวนตัวอย่าง (train = 3 อำเภอ, test = ครบุรี):")
    counts = pd.crosstab(pts["crop"], np.where(is_test, "test", "train"))
    print(counts.to_string())
    for cls, n in counts.get("train", pd.Series(dtype=int)).items():
        if n < MIN_TRAIN:
            print(f"  ⚠ คลาส {cls} มีตัวอย่างเทรนแค่ {n} จุด - ผลของคลาสนี้ยังเชื่อถือไม่ได้")

    X_te, y_te = X[is_test], y[is_test]                     # ทดสอบ: ใช้จุดเดียวเหมือนการใช้งานจริง
    train_pts = pts[~is_test]
    X_tr, y_tr = expand_points(train_pts, months)            # เทรน: ขยายจุดเป็น 3x3 แล้วกรอง
    if len(X_tr) == 0 or len(X_te) == 0:
        print("ยังไม่มีข้อมูล train หรือ test พอ - label เพิ่มก่อนแล้วรันใหม่")
        return
    print(f"\nขยายจุดเทรน: {len(train_pts)} จุด -> {len(X_tr)} พิกเซล "
          f"(เฉลี่ย {len(X_tr) / max(len(train_pts), 1):.1f} พิกเซล/จุด, เต็มที่ 9)")

    # ---------- 2) เทรน ----------
    model = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                   min_samples_leaf=2, n_jobs=-1, random_state=42)
    model.fit(X_tr, y_tr)

    # ---------- 3) วัดผลกับครบุรี ----------
    pred = model.predict(X_te)
    labels = sorted(set(y_tr) | set(y_te))
    rep = classification_report(y_te, pred, labels=labels, zero_division=0)
    macro = f1_score(y_te, pred, labels=sorted(set(y_te)), average="macro", zero_division=0)
    acc = (pred == y_te).mean()
    text = (f"ชุดทดสอบ: {TEST_AMPHOE} ({len(y_te)} จุด)\n"
            f"Macro-F1 = {macro:.3f}   Overall accuracy = {acc:.3f}\n\n{rep}")
    (OUT / "report.txt").write_text(text, encoding="utf-8")
    print("\n" + text)

    cm = confusion_matrix(y_te, pred, labels=labels)
    fig, ax = plt.subplots(figsize=(8, 7))
    ConfusionMatrixDisplay(cm, display_labels=labels).plot(ax=ax, cmap="Greens", colorbar=False)
    ax.set_title(f"Confusion matrix · {TEST_AMPHOE} · macro-F1 {macro:.2f}")
    plt.xticks(rotation=45)
    fig.savefig(OUT / "confusion_matrix.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(months, model.feature_importances_, color="#2F7D52")
    ax.set_title("ความสำคัญของแต่ละเดือนต่อการแยกพืช (feature importance)")
    plt.xticks(rotation=45)
    fig.savefig(OUT / "feature_importance.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    # ---------- 4) เทรนใหม่ด้วยข้อมูลทั้งหมด แล้วทำแผนที่ ----------
    # (วัดผลใช้โมเดลที่ไม่เห็นครบุรี / แผนที่ใช้โมเดลที่เห็นทุกจุด เพื่อให้แม่นที่สุด)
    X_all, y_all = expand_points(pts, months)
    model.fit(X_all, y_all)
    classes = list(model.classes_)
    code = {c: i + 1 for i, c in enumerate(classes)}          # 0 = ไม่มีข้อมูล
    pd.DataFrame({"code": [code[c] for c in classes], "crop": classes}).to_csv(
        OUT / "classes.csv", index=False)

    # บันทึกโมเดลไว้ใช้กับข้อมูลใหม่ (ปีอื่น / พื้นที่อื่น) โดยไม่ต้องเทรนซ้ำ
    import joblib
    joblib.dump({"model": model, "months_order": months, "code": code,
                 "macro_f1_test": float(macro), "test_amphoe": TEST_AMPHOE},
                OUT / "rf_model.joblib")
    print(f"บันทึกโมเดล {OUT / 'rf_model.joblib'}")

    prof = profile.copy()
    prof.update(count=1, dtype="uint8", nodata=0, compress="deflate", predictor=1)
    with rasterio.open(STACK) as src, \
         rasterio.open(OUT / "crop_map.tif", "w", **prof) as dmap, \
         rasterio.open(OUT / "confidence.tif", "w", **prof) as dconf:
        dmap.write_colormap(1, {code[c]: (*COLORS.get(c, (200, 200, 200)), 255) for c in classes})
        H, W = src.height, src.width
        step = 1024
        total = int(np.ceil(H / step))
        for r, row in enumerate(range(0, H, step)):
            win = Window(0, row, W, min(step, H - row))
            a = src.read(window=win).astype("float32")          # (12, h, w)
            h, w = a.shape[1:]
            flat = a.reshape(a.shape[0], -1).T
            has = ~np.isnan(flat).all(axis=1)
            out_c = np.zeros(h * w, dtype="uint8")
            out_p = np.zeros(h * w, dtype="uint8")
            if has.any():
                Xf = fill_gaps(flat[has])
                proba = model.predict_proba(Xf)
                out_c[has] = np.array([code[c] for c in classes], dtype="uint8")[proba.argmax(1)]
                out_p[has] = np.clip(proba.max(1) * 100, 1, 100).astype("uint8")
            dmap.write(out_c.reshape(h, w), 1, window=win)
            dconf.write(out_p.reshape(h, w), 1, window=win)
            print(f"\rทำแผนที่ {r + 1}/{total}", end="", flush=True)
    print(f"\nบันทึกผลทั้งหมดที่ {OUT}")


if __name__ == "__main__":
    main()
