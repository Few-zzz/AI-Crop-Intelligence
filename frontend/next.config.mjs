// เบราว์เซอร์เรียกทุกอย่างผ่าน origin เดียว (ไม่ต้องตั้ง CORS):
//   /api/*      -> FastAPI (หลังบ้าน)
//   /titiler/*  -> TiTiler (tile แผนที่)
// หมายเหตุ: ค่า URL ถูกอ่านตอน build (Dockerfile ตั้งเป็นชื่อ service ใน docker compose)
const API_URL = process.env.API_URL || "http://127.0.0.1:8001";
const TITILER_URL = process.env.TITILER_URL || "http://127.0.0.1:8081";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
      { source: "/titiler/:path*", destination: `${TITILER_URL}/:path*` },
    ];
  },
};

export default nextConfig;
