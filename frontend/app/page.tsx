"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { NdviChart, TrendChart } from "@/components/Charts";
import type { Layer } from "@/components/MapView";
import {
  Compare, CropClass, PointInfo, RAI_PER_KM2, Season, StatRow, TrendRow,
  changeTiles, confidenceTiles, cropTiles, fmt, get,
} from "@/lib/api";

// MapLibre ใช้ได้เฉพาะในเบราว์เซอร์ จึงปิดการเรนเดอร์ฝั่งเซิร์ฟเวอร์ของแผนที่
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

type Mode = "view" | "compare" | "trend";
const FIELD_CROPS = ["rice", "sugarcane", "cassava", "maize"];

export default function Page() {
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [classes, setClasses] = useState<CropClass[]>([]);
  const [districts, setDistricts] = useState<GeoJSON.FeatureCollection | null>(null);
  const [trend, setTrend] = useState<TrendRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>("view");
  const [base, setBase] = useState<"osm" | "esri">("osm");
  const [layer, setLayer] = useState<"crop" | "confidence">("crop");
  const [opacity, setOpacity] = useState(0.8);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [runId, setRunId] = useState<number | null>(null);
  const [cmpA, setCmpA] = useState<number | null>(null);
  const [cmpB, setCmpB] = useState<number | null>(null);
  const [showChange, setShowChange] = useState(false);
  const [district, setDistrict] = useState<string>("");
  const [stats, setStats] = useState<StatRow[]>([]);
  const [cmp, setCmp] = useState<Compare | null>(null);
  const [point, setPoint] = useState<PointInfo | null>(null);
  const [marker, setMarker] = useState<[number, number] | null>(null);

  // โหลดข้อมูลตั้งต้น
  useEffect(() => {
    Promise.all([get<Season[]>("/api/seasons"), get<CropClass[]>("/api/classes"),
      get<GeoJSON.FeatureCollection>("/api/districts"), get<TrendRow[]>("/api/trend")])
      .then(([s, c, d, t]) => {
        setSeasons(s); setClasses(c); setDistricts(d); setTrend(t);
        if (s.length) {
          setRunId(s[s.length - 1].id);
          setCmpA(s[Math.max(0, s.length - 2)].id);
          setCmpB(s[s.length - 1].id);
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  const colors = useMemo(() => Object.fromEntries(classes.map((c) => [c.name, c.color])), [classes]);
  const byName = useMemo(() => Object.fromEntries(classes.map((c) => [c.name, c])), [classes]);
  const run = seasons.find((s) => s.id === runId) || null;
  const sA = seasons.find((s) => s.id === cmpA) || null;
  const sB = seasons.find((s) => s.id === cmpB) || null;

  useEffect(() => { if (runId) get<StatRow[]>(`/api/runs/${runId}/stats`).then(setStats).catch(() => setStats([])); }, [runId]);
  useEffect(() => {
    if (mode === "compare" && cmpA && cmpB) {
      const dq = district ? `&district=${encodeURIComponent(district)}` : "";
      get<Compare>(`/api/compare?a=${cmpA}&b=${cmpB}${dq}`).then(setCmp).catch(() => setCmp(null));
    }
  }, [mode, cmpA, cmpB, district]);
  useEffect(() => {
    const dq = district ? `?district=${encodeURIComponent(district)}` : "";
    get<TrendRow[]>(`/api/trend${dq}`).then(setTrend).catch(() => {});
  }, [district]);

  // ชั้นแผนที่ของแต่ละโหมด
  const mainLayers: Layer[] = useMemo(() => {
    const s = mode === "compare" ? sA : run;
    if (!s) return [];
    const tiles = mode !== "compare" && layer === "confidence" ? confidenceTiles(s) : cropTiles(s, colors, hidden);
    return [{ id: "main", tiles, opacity, bounds: s.bounds }];
  }, [mode, run, sA, layer, colors, hidden, opacity]);
  const rightLayers: Layer[] | undefined = useMemo(() => {
    if (mode !== "compare" || !sB) return undefined;
    // โหมดพิกเซลที่เปลี่ยน: แสดงเฉพาะชั้น change (สีตามชนิดพืชในฤดูกาลขวา) ไม่ซ้อนแผนที่ชนิดพืชเต็ม
    if (showChange && sA) return [{ id: "change", tiles: changeTiles(sA.id, sB.id), opacity, bounds: sB.bounds }];
    return [{ id: "main", tiles: cropTiles(sB, colors, hidden), opacity, bounds: sB.bounds }];
  }, [mode, sA, sB, colors, hidden, opacity, showChange]);

  const onClick = useCallback((lng: number, lat: number) => {
    const target = mode === "compare" ? cmpB : runId;
    if (!target) return;
    setMarker([lng, lat]);
    get<PointInfo>(`/api/point?run=${target}&lon=${lng}&lat=${lat}`).then(setPoint).catch(() => setPoint(null));
  }, [mode, cmpB, runId]);

  const toggle = (name: string) => setHidden((h) => { const n = new Set(h); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const districtNames = (districts?.features || []).map((f) => f.properties as { name_th: string; role: string });

  // ตารางพื้นที่ (โหมดดู)
  const areaRows = useMemo(() => {
    const sum: Record<string, number> = {};
    stats.filter((r) => !district || r.district === district).forEach((r) => { sum[r.class] = (sum[r.class] || 0) + r.area_km2; });
    const total = Object.values(sum).reduce((a, b) => a + b, 0);
    return { total, rows: Object.entries(sum).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]) };
  }, [stats, district]);

  return (
    <div className="app">
      <div style={{ position: "relative" }}>
        <MapView base={base} left={mainLayers} right={rightLayers} districts={districts}
          fitBounds={seasons[0]?.bounds} onClick={onClick} marker={marker} />
        {mode === "compare" && sA && sB && (
          <>
            <div className="maplabel" style={{ left: 50 }}>{sA.season_label}</div>
            <div className="maplabel" style={{ right: 10 }}>{sB.season_label}{showChange ? " · พิกเซลที่เปลี่ยน" : ""}</div>
          </>
        )}
      </div>

      <aside>
        <header>
          <h1>AI Crop Intelligence</h1>
          <p>แผนที่ชนิดพืชรายฤดูกาลเพาะปลูก จากภาพดาวเทียม Sentinel-2 · จังหวัดนครราชสีมา 4 อำเภอ</p>
        </header>
        {error && <section><div className="warn">เชื่อมต่อหลังบ้านไม่ได้: {error}</div></section>}

        <div className="tabs" role="tablist">
          {([["view", "ดูรายฤดูกาล"], ["compare", "เทียบ 2 ฤดูกาล"], ["trend", "แนวโน้ม"]] as [Mode, string][]).map(([m, t]) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>{t}</button>
          ))}
        </div>

        <section>
          {mode === "view" && (
            <div className="row">
              <span>ฤดูกาล</span>
              <select value={runId ?? ""} onChange={(e) => { setRunId(+e.target.value); setPoint(null); }}>
                {seasons.map((s) => <option key={s.id} value={s.id}>{s.season_label} (พ.ค.–เม.ย.)</option>)}
              </select>
            </div>
          )}
          {mode === "compare" && (
            <>
              <div className="row">
                <span>ซ้าย</span>
                <select value={cmpA ?? ""} onChange={(e) => setCmpA(+e.target.value)}>
                  {seasons.map((s) => <option key={s.id} value={s.id}>{s.season_label}</option>)}
                </select>
                <span>ขวา</span>
                <select value={cmpB ?? ""} onChange={(e) => setCmpB(+e.target.value)}>
                  {seasons.map((s) => <option key={s.id} value={s.id}>{s.season_label}</option>)}
                </select>
              </div>
              <label><input type="checkbox" checked={showChange} onChange={(e) => setShowChange(e.target.checked)} />
                แสดงเฉพาะพิกเซลที่เปลี่ยนชนิดพืช (ฝั่งขวา)</label>
              {seasons.length < 2 && <p className="warn">ตอนนี้มีข้อมูลฤดูกาลเดียว รัน 07_predict.py ฤดูกาลอื่นเพิ่มก่อนจึงจะเทียบได้</p>}
              <p className="note">ลากแถบเลื่อนด้านล่างแผนที่เพื่อเลื่อนเส้นแบ่ง</p>
            </>
          )}
          <div className="row">
            <span>อำเภอ</span>
            <select value={district} onChange={(e) => setDistrict(e.target.value)}>
              <option value="">รวม 4 อำเภอ</option>
              {districtNames.map((d) => <option key={d.name_th} value={d.name_th}>{d.name_th}{d.role === "test" ? " (พื้นที่ทดสอบ)" : ""}</option>)}
            </select>
          </div>
        </section>

        {mode !== "trend" && (
          <section>
            <h2>ชั้นข้อมูล</h2>
            <div className="row">
              <span>แผนที่ฐาน</span>
              <span className="seg">
                <button className={base === "osm" ? "on" : ""} onClick={() => setBase("osm")}>แผนที่</button>
                <button className={base === "esri" ? "on" : ""} onClick={() => setBase("esri")}>ภาพถ่ายดาวเทียม</button>
              </span>
            </div>
            {mode === "view" && (
              <div className="row">
                <label><input type="radio" checked={layer === "crop"} onChange={() => setLayer("crop")} /> ชนิดพืช</label>
                <label><input type="radio" checked={layer === "confidence"} onChange={() => setLayer("confidence")} /> ความมั่นใจของโมเดล</label>
              </div>
            )}
            <div className="row">
              <span>ความทึบ</span>
              <input type="range" min={0} max={100} value={opacity * 100} onChange={(e) => setOpacity(+e.target.value / 100)} />
              <span style={{ width: 36, textAlign: "right" }}>{Math.round(opacity * 100)}%</span>
            </div>
          </section>
        )}

        {(mode !== "view" || layer === "crop") ? (
          <section>
            <h2>คลาส (ติ๊กเพื่อซ่อน/แสดง)</h2>
            <div className="legend">
              {classes.map((c) => (
                <label key={c.name}><input type="checkbox" checked={!hidden.has(c.name)} onChange={() => toggle(c.name)} />
                  <span className="sw" style={{ background: c.color }} />{c.name_th}</label>
              ))}
            </div>
            <div className="row" style={{ fontSize: 12.5 }}>
              <button className="link" onClick={() => setHidden(new Set())}>แสดงทั้งหมด</button>
              <button className="link" onClick={() => setHidden(new Set(classes.filter((c) => !FIELD_CROPS.includes(c.name)).map((c) => c.name)))}>เฉพาะพืชไร่</button>
            </div>
          </section>
        ) : (
          <section>
            <h2>ความมั่นใจ (ความน่าจะเป็นของคลาสที่ชนะ)</h2>
            <div className="grad" />
            <div className="grad-lbl"><span>0 ต่ำ</span><span>50</span><span>100 สูง</span></div>
            <p className="note">สีแดง = โมเดลไม่แน่ใจ ควรเก็บข้อมูลอ้างอิงเพิ่มบริเวณนั้น</p>
          </section>
        )}

        {mode !== "trend" && (
          <section>
            <h2>จุดที่คลิก{mode === "compare" && sB ? ` (ฤดูกาล ${sB.season_label})` : ""}</h2>
            {!point ? <p className="empty">คลิกบนแผนที่เพื่อดูชนิดพืช ความมั่นใจ และกราฟ NDVI ของพิกเซลนั้น</p>
              : !point.inside || !point.class ? <p className="empty">จุดนี้ไม่มีผลการจำแนก</p>
              : (
                <>
                  <div className="big"><span className="sw" style={{ background: point.color || "#ccc", width: 18, height: 18 }} />{point.name_th}</div>
                  <dl className="kv">
                    <dt>ความมั่นใจ</dt><dd>{point.confidence} / 100</dd>
                    <dt>อำเภอ</dt><dd>{point.district || "–"}{point.district_role === "test" && <span className="tag">พื้นที่ทดสอบ</span>}</dd>
                  </dl>
                  {point.months && point.ndvi && <NdviChart months={point.months} ndvi={point.ndvi} />}
                  <p className="note">แถบเทา = เดือนที่เมฆบังทั้งเดือน (โมเดลเติมค่าก่อนทำนาย)</p>
                </>
              )}
          </section>
        )}

        {mode === "view" && run && (
          <section>
            <h2>พื้นที่ตามผลจำแนก · ฤดูกาล {run.season_label}</h2>
            <dl className="kv">
              <dt>พื้นที่ที่จำแนก</dt><dd>{fmt(areaRows.total, 1)} ตร.กม. ({fmt(areaRows.total * RAI_PER_KM2)} ไร่)</dd>
              <dt>โมเดล</dt><dd>{run.model_name} {run.model_version} · Macro-F1 {run.model_macro_f1?.toFixed(2) ?? "–"}</dd>
              <dt>เดือนที่มีภาพ</dt><dd>{run.months_with_data} / 12</dd>
            </dl>
            <table>
              <thead><tr><th>คลาส</th><th>ตร.กม.</th><th>ไร่</th><th>%</th></tr></thead>
              <tbody>
                {areaRows.rows.map(([cls, km2]) => (
                  <tr key={cls}>
                    <td><span className="sw" style={{ background: colors[cls] }} /> {byName[cls]?.name_th || cls}</td>
                    <td>{fmt(km2, 1)}</td><td>{fmt(km2 * RAI_PER_KM2)}</td><td>{fmt((100 * km2) / areaRows.total, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">นับจากจำนวนพิกเซล ยังไม่ได้ปรับตามความคลาดเคลื่อนของโมเดล (Olofsson et al., 2014) · Macro-F1 วัดบนพื้นที่ทดสอบของฤดูกาลที่ใช้เทรน ไม่ใช่ของฤดูกาลนี้</p>
          </section>
        )}

        {mode === "view" && run && (
          <section>
            <h2>ดาวน์โหลดผล · ฤดูกาล {run.season_label}</h2>
            <div className="dl">
              <a href={`/api/runs/${run.id}/download/stats.csv`} download>
                <b>ตารางพื้นที่และ % (CSV)</b><small>รายอำเภอ + รวม · เปิดด้วย Excel</small></a>
              <a href={`/api/runs/${run.id}/download/map.png`} download>
                <b>ภาพแผนที่ (PNG)</b><small>สำหรับแปะรายงาน/สไลด์</small></a>
              <a href={`/api/runs/${run.id}/download/crop_map.tif`} download>
                <b>แผนที่ชนิดพืช (GeoTIFF)</b><small>20 ม. · เปิดด้วย QGIS</small></a>
              <a href={`/api/runs/${run.id}/download/confidence.tif`} download>
                <b>ความมั่นใจ (GeoTIFF)</b><small>ค่า 0–100 ต่อพิกเซล</small></a>
            </div>
            <p className="note">ไฟล์ครอบคลุมทั้ง 4 อำเภอและทุกคลาส ไม่ขึ้นกับตัวกรองอำเภอ/คลาสที่เลือกอยู่</p>
          </section>
        )}

        {mode === "compare" && cmp && (
          <section>
            <h2>เปรียบเทียบพื้นที่ · {cmp.a.label} → {cmp.b.label}</h2>
            <table>
              <thead><tr><th>คลาส</th><th>{cmp.a.label}</th><th>{cmp.b.label}</th><th>เปลี่ยน</th></tr></thead>
              <tbody>
                {cmp.classes.filter((r) => r.area_a + r.area_b > 0).map((r) => (
                  <tr key={r.class}>
                    <td><span className="sw" style={{ background: r.color }} /> {r.name_th}</td>
                    <td>{fmt(r.area_a, 1)}</td><td>{fmt(r.area_b, 1)}</td>
                    <td className={r.diff_km2 >= 0 ? "up" : "down"}>
                      {r.diff_km2 >= 0 ? "+" : ""}{fmt(r.diff_km2, 1)}{r.diff_pct != null ? ` (${r.diff_pct >= 0 ? "+" : ""}${fmt(r.diff_pct, 0)}%)` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">หน่วย ตร.กม.</p>
            {cmp.transitions && (
              <>
                <h2 style={{ marginTop: 12 }}>การเปลี่ยนชนิดพืชที่พบมาก</h2>
                <p className="note" style={{ marginTop: 0 }}>เปลี่ยนชนิดรวม {fmt(cmp.transitions.changed_km2, 1)} ตร.กม. ({fmt((100 * cmp.transitions.changed_km2) / Math.max(cmp.transitions.total_km2, 1), 1)}% ของพื้นที่)</p>
                <table>
                  <tbody>
                    {cmp.transitions.top.slice(0, 8).map((t) => (
                      <tr key={`${t.from}-${t.to}`}>
                        <td>{byName[t.from]?.name_th || t.from} → {byName[t.to]?.name_th || t.to}</td>
                        <td>{fmt(t.area_km2, 1)} ตร.กม.</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <p className="note">ส่วนหนึ่งของการเปลี่ยนแปลงอาจมาจากความผิดพลาดของโมเดล ไม่ใช่การเปลี่ยนพืชจริงทั้งหมด</p>
          </section>
        )}

        {mode === "trend" && (
          <section>
            <h2>แนวโน้มพื้นที่แต่ละชนิดพืช{district ? ` · ${district}` : " · รวม 4 อำเภอ"}</h2>
            {seasons.length < 2 && <p className="warn">มีข้อมูลฤดูกาลเดียว กราฟจะมีจุดเดียวจนกว่าจะทำนายฤดูกาลอื่นเพิ่ม</p>}
            <TrendChart rows={trend} hidden={hidden} />
            <p className="note">ติ๊กคลาสด้านบนเพื่อซ่อน/แสดงเส้นในกราฟ · พื้นที่นับจากจำนวนพิกเซล</p>
          </section>
        )}
      </aside>
    </div>
  );
}
