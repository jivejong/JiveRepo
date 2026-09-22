# syntax=docker/dockerfile:1

# ---- client build: compile the React/Vite app to dist/ ----
FROM node:22-alpine AS client
WORKDIR /client
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js transpose.js ./
COPY src ./src
COPY server/chartParser.js ./server/chartParser.js
COPY public ./public
RUN npm run build          # -> /client/dist

# ---- server deps: production node_modules only ----
FROM node:22-alpine AS deps
WORKDIR /app
COPY server/package.json server/package-lock.json ./
RUN npm ci --omit=dev

# ---- runtime: API server + built client, non-root ----
FROM node:22-alpine AS runtime
ENV NODE_ENV=production
WORKDIR /app
RUN apk add --no-cache curl

COPY --chown=node:node --from=deps   /app/node_modules ./node_modules
COPY --chown=node:node server        ./server
COPY --chown=node:node --from=client /client/dist ./dist
USER node

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:3000/healthz || exit 1

CMD ["node", "server/server.js"]
