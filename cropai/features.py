"""การเตรียม feature ที่ต้องเหมือนกันทั้งตอนเทรนและตอนทำนาย"""
import pandas as pd


def fill_gaps(X):
    """เติมเดือนที่เมฆบัง (NaN) ด้วยค่าเฉลี่ยของเดือนก่อน-หลังที่มีข้อมูล (เหมือน 05_train_rf.py)"""
    df = pd.DataFrame(X)
    f, b = df.ffill(axis=1), df.bfill(axis=1)
    out = (f + b) / 2
    out = out.fillna(f).fillna(b)
    return out.to_numpy(dtype="float32")
