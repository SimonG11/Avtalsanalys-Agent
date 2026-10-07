# The web app as a container image, in three stages:
#   deps    installs the packages from package-lock.json
#   build   builds Next.js into a self-contained server (.next/standalone)
#   runner  the image that runs: only the standalone server and its static files
# A fourth target, mock, runs the mock agent (mock/server.ts) for demos without the backend.
# The Node image is multi-architecture, so the same file builds on a Mac with Apple silicon
# (linux/arm64) and in CI (linux/amd64).

FROM node:22.22.0-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS build
ENV NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npm run build

FROM node:22.22.0-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    COPILOTKIT_TELEMETRY_DISABLED=true \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]

# The mock needs three packages, not the web app's whole dependency tree, so it gets its own
# small install. Keep the versions the same as in package.json.
FROM node:22.22.0-bookworm-slim AS mock
WORKDIR /app
RUN npm install --no-save --no-audit --no-fund @ag-ui/core@1.0.1 @ag-ui/encoder@1.0.1 pdf-lib@1.17.1
COPY package.json ./
COPY mock ./mock
ENV MOCK_PORT=8000
USER node
EXPOSE 8000
CMD ["node", "mock/server.ts"]
