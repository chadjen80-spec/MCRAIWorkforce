# Next.js dashboard (standalone output).
FROM node:24-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# NEXT_PUBLIC_* values are compiled into the bundle at build time (1 shows the Smart Café menu).
ARG NEXT_PUBLIC_CAFE_ENABLED=0
ENV NEXT_PUBLIC_CAFE_ENABLED=$NEXT_PUBLIC_CAFE_ENABLED
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./
RUN npm run build

FROM node:24-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S mcr && adduser -S mcr -G mcr
COPY --from=build --chown=mcr:mcr /app/.next/standalone ./
COPY --from=build --chown=mcr:mcr /app/.next/static ./.next/static
USER mcr
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/login >/dev/null || exit 1
CMD ["node", "server.js"]
