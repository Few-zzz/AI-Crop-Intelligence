import "./globals.css";

export const metadata = {
  title: "AI Crop Intelligence",
  description: "แผนที่ชนิดพืชรายฤดูกาลจากภาพดาวเทียม Sentinel-2 จังหวัดนครราชสีมา",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="th">
      <body>{children}</body>
    </html>
  );
}
