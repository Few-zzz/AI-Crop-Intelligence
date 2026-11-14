// ชนิดข้อมูลและฟังก์ชันเรียกหลังบ้าน
export type Season = {
  id: number;
  season: number;
  season_label: string;
  month_start: string;
  month_end: string;
  months_with_data: number;
  model_name: string;
  model_version: string;
  model_macro_f1: number | null;
  model_accuracy: number | null;
  season_macro_f1: number | null;
  class_codes: Record<string, string>;
  crop_cog: string;
  confidence_cog: string;
  bounds: [number, number, number, number];
  pixel_area_m2: number;
};
export type CropClass = { id: number; name: string; name_th: string; color: string };
export type StatRow = {
  district: string; role: string; class: string; name_th: string; color: string;
  area_km2: number; mean_conf: number | null;
};
export type CompareRow = {
  class: string; name_th: string; color: string;
  area_a: number; area_b: number; diff_km2: number; diff_pct: number | null;
};
export type Transition = { from: string; to: string; area_km2: number };
export type Compare = {
  a: { id: number; label: string }; b: { id: number; label: string };
  classes: CompareRow[];
  transitions: { changed_km2: number; total_km2: number; top: Transition[] } | null;
};
export type TrendRow = { run_id: number; season: number; season_label: string; class: string;
  name_th: string; color: string; area_km2: number };
export type PointInfo = {
  inside: boolean; district?: string | null; district_role?: string | null; class?: string | null;
  name_th?: string | null; color?: string | null; confidence?: number | null;
  months?: string[]; ndvi?: (number | null)[];
};

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// URL ของ tile ผ่าน TiTiler (ต้องเป็น URL เต็มสำหรับ MapLibre)
export function cropTiles(s: Season, colors: Record<string, string>, hidden: Set<string>) {
  const cmap: Record<string, number[]> = {};
  for (const [code, name] of Object.entries(s.class_codes)) {
    if (hidden.has(name)) continue;
    const c = colors[name] || "#c8c8c8";
    cmap[code] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)).concat(255);
  }
  if (!Object.keys(cmap).length) cmap["255"] = [0, 0, 0, 0];   // ซ่อนทุกคลาส
  return `${location.origin}/titiler/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodeURIComponent(s.crop_cog)}&colormap=${encodeURIComponent(JSON.stringify(cmap))}`;
}
export function confidenceTiles(s: Season) {
  return `${location.origin}/titiler/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodeURIComponent(s.confidence_cog)}&colormap_name=rdylgn&rescale=0,100`;
}
export function changeTiles(a: number, b: number) {
  return `${location.origin}/api/change/${a}/${b}/{z}/{x}/{y}.png`;
}

export const TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
export const fmt = (v: number, d = 0) =>
  v.toLocaleString("th-TH", { minimumFractionDigits: d, maximumFractionDigits: d });
export const RAI_PER_KM2 = 625;
