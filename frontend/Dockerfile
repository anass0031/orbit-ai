# ORBIT AI frontend — Next.js standalone build.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S orbit && adduser -S orbit -G orbit
COPY --from=build --chown=orbit:orbit /app/.next/standalone ./
COPY --from=build --chown=orbit:orbit /app/.next/static ./.next/static
# Static files (login background video…): the standalone server does not include public/ by itself
COPY --from=build --chown=orbit:orbit /app/public ./public
USER orbit
EXPOSE 3000
CMD ["node", "server.js"]
