# Web container: builds the PWA, then serves it with Caddy, which also proxies /api
# to the API container and can obtain HTTPS certificates automatically.
# Build context: repository root.
FROM node:22-alpine AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH
RUN corepack enable
WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-workspace.yaml frontend/pnpm-lock.yaml frontend/.npmrc frontend/tsconfig.base.json ./
COPY frontend/apps/web/package.json apps/web/
COPY frontend/packages/tokens/package.json packages/tokens/
COPY frontend/packages/i18n/package.json packages/i18n/
COPY frontend/packages/api-client/package.json packages/api-client/
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN pnpm build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/apps/web/dist /srv
EXPOSE 80 443
