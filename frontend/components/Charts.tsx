"use client";
// กราฟ SVG เขียนเอง (ไม่ต้องลงไลบรารีกราฟเพิ่ม)
import { TH_MONTH, TrendRow, fmt } from "@/lib/api";

const monthLabel = (m: string) => TH_MONTH[+m.slice(5, 7) - 1] || m;

export function NdviChart({ months, ndvi }: { months: string[]; ndvi: (number | null)[] }) {
  const W = 340, H = 170, L = 32, R = 8, T = 10, B = 26;
  const n = ndvi.length;
  const x = (i: number) => L + (i * (W - L - R)) / Math.max(n - 1, 1);
  const y = (v: number) => T + (1 - (v + 0.1) / 1.1) * (H - T - B);
  let d = "", pen = false;
  ndvi.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="กราฟ NDVI รายเดือน">
      {[0, 0.2, 0.4, 0.6, 0.8, 1].map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" />
          <text x={L - 4} y={y(v) + 3} textAnchor="end" className="tick">{v.toFixed(1)}</text>
        </g>
      ))}
      {ndvi.map((v, i) => v == null &&
        <rect key={`g${i}`} x={x(i) - 6} y={T} width={12} height={H - T - B} className="gap" />)}
      {months.map((m, i) => (
        <text key={m} x={x(i)} y={H - B + 13} textAnchor="middle" className="tick">{monthLabel(m)}</text>
      ))}
      <path d={d} className="line" />
      {ndvi.map((v, i) => v != null && <circle key={i} cx={x(i)} cy={y(v)} r={3} className="dot"><title>{`${months[i]}: ${v.toFixed(3)}`}</title></circle>)}
    </svg>
  );
}

export function TrendChart({ rows, hidden }: { rows: TrendRow[]; hidden: Set<string> }) {
  const seasons = [...new Map(rows.map((r) => [r.season, r.season_label])).entries()].sort((a, b) => a[0] - b[0]);
  const classes = [...new Map(rows.map((r) => [r.class, r])).values()].filter((c) => !hidden.has(c.class));
  const W = 340, H = 220, L = 46, R = 10, T = 10, B = 28;
  const max = Math.max(1, ...rows.filter((r) => !hidden.has(r.class)).map((r) => r.area_km2));
  const x = (i: number) => (seasons.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (seasons.length - 1));
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const val = (cls: string, s: number) => rows.find((r) => r.class === cls && r.season === s)?.area_km2;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="กราฟแนวโน้มพื้นที่">
      {[0, 0.25, 0.5, 0.75, 1].map((f) => (
        <g key={f}>
          <line x1={L} x2={W - R} y1={y(max * f)} y2={y(max * f)} className="grid" />
          <text x={L - 4} y={y(max * f) + 3} textAnchor="end" className="tick">{fmt(max * f)}</text>
        </g>
      ))}
      {seasons.map(([s, label], i) => (
        <text key={s} x={x(i)} y={H - B + 15} textAnchor="middle" className="tick">{label}</text>
      ))}
      {classes.map((c) => {
        const pts = seasons.map(([s], i) => [x(i), val(c.class, s)] as const).filter((p) => p[1] != null);
        return (
          <g key={c.class}>
            <path d={pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${y(p[1]!)}`).join("")}
              stroke={c.color} strokeWidth={2.2} fill="none" />
            {pts.map((p) => <circle key={p[0]} cx={p[0]} cy={y(p[1]!)} r={3.5} fill={c.color}>
              <title>{`${c.name_th}: ${fmt(p[1]!, 1)} ตร.กม.`}</title></circle>)}
          </g>
        );
      })}
      <text x={4} y={T + 4} className="tick">ตร.กม.</text>
    </svg>
  );
}
