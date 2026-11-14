# หน้าบ้าน Next.js แบบ standalone (ขนาด image เล็ก รันด้วย node server.js)
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# ที่อยู่ของ service อื่นในเครือข่ายของ docker compose (ถูกอ่านตอน build)
ENV API_URL=http://api:8000 \
    TITILER_URL=http://titiler:8000 \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
