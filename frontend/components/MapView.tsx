"use client";
// แผนที่ MapLibre: โหมดดูฤดูกาลเดียว หรือโหมดเทียบ (แผนที่สองชั้นซ้อนกัน เลื่อนเส้นแบ่งซ้าย-ขวา)
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";

export type Layer = { id: string; tiles: string; opacity: number; bounds?: number[] };

type Props = {
  base: "osm" | "esri";
  left: Layer[];                  // ชั้นของแผนที่หลัก (หรือฝั่งซ้ายในโหมดเทียบ)
  right?: Layer[];                // มีค่า = โหมดเทียบ
  districts: GeoJSON.FeatureCollection | null;
  fitBounds?: number[];
  onClick?: (lng: number, lat: number) => void;
  marker?: [number, number] | null;
};

const style = (): maplibregl.StyleSpecification => ({
  version: 8,
  sources: {
    osm: { type: "raster", tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"] },
    esri: { type: "raster", tileSize: 256, maxzoom: 19, attribution: "Imagery © Esri",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"] },
  },
  layers: [
    { id: "osm", type: "raster", source: "osm" },
    { id: "esri", type: "raster", source: "esri", layout: { visibility: "none" } },
  ],
});

function useMap(container: React.RefObject<HTMLDivElement | null>) {
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  useEffect(() => {
    if (!container.current) return;
    const m = new maplibregl.Map({ container: container.current, style: style(), center: [102.1, 14.9], zoom: 8 });
    m.on("load", () => { m.resize(); setMap(m); });
    // CSS ของแผนที่โหลดตามมาทีหลัง กล่องจึงอาจสูง 0 ตอนสร้าง -> ปรับขนาดเมื่อกล่องเปลี่ยน
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(container.current);
    if (process.env.NODE_ENV !== "production") {      // ไว้ตรวจสอบผ่าน devtools ตอนพัฒนา
      const w = window as unknown as { __cropMaps?: maplibregl.Map[] };
      (w.__cropMaps ||= []).push(m);
    }
    return () => { ro.disconnect(); m.remove(); };
  }, [container]);
  return map;
}

// ทำให้ชั้นข้อมูลบนแผนที่ตรงกับ props (เพิ่ม/แก้/ลบ)
function syncLayers(m: maplibregl.Map, layers: Layer[], base: string, districts: GeoJSON.FeatureCollection | null) {
  m.setLayoutProperty("osm", "visibility", base === "osm" ? "visible" : "none");
  m.setLayoutProperty("esri", "visibility", base === "esri" ? "visible" : "none");
  const want = new Set(layers.map((l) => l.id));
  for (const l of m.getStyle().layers) {
    if (l.id.startsWith("data-") && !want.has(l.id.slice(5))) {
      m.removeLayer(l.id);
      m.removeSource(l.id);
    }
  }
  for (const l of layers) {
    const id = `data-${l.id}`;
    const src = m.getSource(id) as maplibregl.RasterTileSource | undefined;
    if (!src) {
      m.addSource(id, { type: "raster", tiles: [l.tiles], tileSize: 256, maxzoom: 15,
        ...(l.bounds ? { bounds: l.bounds as [number, number, number, number] } : {}) });
      m.addLayer({ id, type: "raster", source: id,
        paint: { "raster-opacity": l.opacity, "raster-resampling": "nearest", "raster-fade-duration": 0 } },
        m.getLayer("aoi-line") ? "aoi-line" : undefined);
    } else {
      if ((src as unknown as { tiles?: string[] }).tiles?.[0] !== l.tiles) src.setTiles([l.tiles]);
      m.setPaintProperty(id, "raster-opacity", l.opacity);
    }
  }
  if (districts && !m.getSource("aoi")) {
    m.addSource("aoi", { type: "geojson", data: districts });
    m.addLayer({ id: "aoi-line", type: "line", source: "aoi", filter: ["!=", ["get", "role"], "test"],
      paint: { "line-color": "#222", "line-width": 1.6 } });
    m.addLayer({ id: "aoi-test", type: "line", source: "aoi", filter: ["==", ["get", "role"], "test"],
      paint: { "line-color": "#d9480f", "line-width": 2.4, "line-dasharray": [3, 2] } });
  }
}

export default function MapView({ base, left, right, districts, fitBounds, onClick, marker }: Props) {
  const refA = useRef<HTMLDivElement>(null);
  const refB = useRef<HTMLDivElement>(null);
  const mapA = useMap(refA);
  const mapB = useMap(refB);
  const [split, setSplit] = useState(50);
  const compare = !!right;
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const fitted = useRef(false);

  useEffect(() => { if (mapA) syncLayers(mapA, left, base, districts); }, [mapA, left, base, districts]);
  useEffect(() => { if (mapB) syncLayers(mapB, right || [], base, districts); }, [mapB, right, base, districts]);

  useEffect(() => {
    if (mapA && fitBounds && !fitted.current) {
      mapA.fitBounds(fitBounds as [number, number, number, number], { padding: 30, duration: 0 });
      fitted.current = true;
    }
  }, [mapA, fitBounds]);

  // ให้สองแผนที่เลื่อน/ซูมไปพร้อมกัน
  useEffect(() => {
    if (!mapA || !mapB) return;
    let busy = false;
    const sync = (from: maplibregl.Map, to: maplibregl.Map) => () => {
      if (busy) return;
      busy = true;
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() });
      busy = false;
    };
    const ab = sync(mapA, mapB), ba = sync(mapB, mapA);
    mapA.on("move", ab);
    mapB.on("move", ba);
    ab();
    return () => { mapA.off("move", ab); mapB.off("move", ba); };
  }, [mapA, mapB]);

  useEffect(() => {
    const top = compare ? mapB : mapA;
    if (!top || !onClick) return;
    const h = (e: maplibregl.MapMouseEvent) => onClick(e.lngLat.lng, e.lngLat.lat);
    top.on("click", h);
    if (compare && mapA) mapA.on("click", h);
    return () => { top.off("click", h); if (compare && mapA) mapA.off("click", h); };
  }, [mapA, mapB, compare, onClick]);

  useEffect(() => {
    markerRef.current?.remove();
    const m = compare ? mapB : mapA;
    if (m && marker) markerRef.current = new maplibregl.Marker({ color: "#111" }).setLngLat(marker).addTo(m);
  }, [marker, mapA, mapB, compare]);

  useEffect(() => { mapA?.resize(); mapB?.resize(); }, [compare, mapA, mapB]);

  return (
    <div className="mapwrap">
      <div ref={refA} className="map" />
      <div ref={refB} className="map top"
        style={{ visibility: compare ? "visible" : "hidden", clipPath: `inset(0 0 0 ${split}%)` }} />
      {compare && (
        <>
          <div className="splitline" style={{ left: `${split}%` }} />
          <input className="splitter" type="range" min={0} max={100} value={split}
            onChange={(e) => setSplit(+e.target.value)} aria-label="เลื่อนเส้นแบ่งแผนที่" />
        </>
      )}
    </div>
  );
}
