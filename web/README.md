# Webbappen

Chatt med agentens tankar och steg live, svar med källor och en källpanel med PDF:en där citatet
är markerat, i ljust och mörkt läge. Next.js och CopilotKit, som pratar med agenten över AG-UI. Förklaringen av varje del finns i
[`docs/steg/10-webbapp.md`](../docs/steg/10-webbapp.md) och beslutet i
[ADR 0010](../docs/adr/0010-webbapp-copilotkit-ag-ui.md).

## Prova utan backend

Mocken i `mock/` svarar som agenten, med skriptade svar och en påhittad PDF.

```bash
docker compose -f web/compose.mock.yaml up --build   # från repots rot
```

Öppna http://localhost:3000. Frågor att prova finns i
[`docs/steg/10-webbapp.md`](../docs/steg/10-webbapp.md#så-verifierar-du-m10-själv).

## Utveckla

Du behöver Node 22.18 eller senare.

```bash
npm ci
npm run mock   # mocken på http://localhost:8000
npm run dev    # webbappen på http://localhost:3000, i ett eget fönster
```

Mot den riktiga agenten startar du API:t i stället för mocken. Webbappen hittar API:t via
`API_URL` (standard `http://localhost:8000`).

## Kommandon

| Kommando | Vad det gör |
|---|---|
| `npm run dev` | Startar webbappen i utvecklingsläge |
| `npm run mock` | Startar mocken (`MOCK_PORT`, standard 8000; `MOCK_FAST=1` utan pauser) |
| `npm run build` / `npm start` | Bygger och startar som i produktion |
| `npm run format:check` | Prettier |
| `npm run lint` | ESLint |
| `npm run typecheck` | TypeScript |
| `npm test` | Enhetstester (`src/lib/`, `mock/`) |
| `npm run test:e2e` | Webbläsartester mot mocken, efter `npm run build`. `E2E_BASE_URL` kör dem mot en app som redan är igång |

## Struktur

| Mapp | Innehåll |
|---|---|
| `src/app/` | Sidan och två routes: `api/copilotkit` (agenten) och `api/documents/[sha256]/pdf` |
| `src/components/` | Chatten, tidslinjen, agentens fråga, svarskortet och källpanelen |
| `src/lib/` | Ren logik med tester: kontraktet, frågorna och tidslinjen, verktygens etiketter, hänvisningar och markering av citat |
| `mock/` | Mocken: server, skriptade körningar och test-PDF:en |
| `e2e/` | Webbläsartester med Playwright |
