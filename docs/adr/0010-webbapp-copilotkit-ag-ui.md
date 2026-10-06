# ADR 0010: Webbappen med Next.js och CopilotKit över AG-UI

**Status:** Föreslaget (M10).

## Kontext

Arkitekturen (avsnitt 5 i arkitekturplanen) säger att användaren ska se agentens steg live, att
ett klick på en källa ska öppna PDF:en på rätt sida med citatet markerat, och att agenten ska kunna
fråga användaren mitt i en körning. Valideringen (fas 3) valde protokollet AG-UI mellan agent och
webbapp. Backend använder `ag-ui-langgraph`, som gör en LangGraph-graf till en AG-UI-tjänst i
FastAPI.

Det som avgör valet:

- **Backend och webbapp byggs samtidigt** i olika trådar. Webbappen måste gå att bygga och testa
  innan agenten finns, mot ett kontrakt som båda följer.
- **Svaret är strukturerat.** Ett svar har status och källor med dokument, avsnitt, sida och citat.
  Det är data, inte bara text i chatten.
- **Citaten ska gå att kontrollera med ögonen.** Det kräver PDF:ens textlager i webbläsaren, inte
  bara en bild av sidan.
- **Demot körs på en Mac med Apple silicon** med Docker.

## Beslut

1. **Next.js 16 med App Router och CopilotKit 1.77 (v2-API:t).** CopilotKit har färdiga delar för
   AG-UI: en chatt, `useRenderTool` för verktygsanrop, `useInterrupt` för LangGraphs interrupt och
   `useAgent` för agentens tillstånd. Delarna används med egna komponenter för stegen, svarskortet,
   källpanelen och dialogen.
2. **Webbläsaren pratar bara med webbappen.** En route i Next.js kör CopilotKits runtime med en
   `HttpAgent` mot API:ts `/agui`, och en annan skickar vidare PDF:erna. API:ts adress (`API_URL`)
   läses medan servern kör, så samma image fungerar i alla miljöer, och API:t behöver ingen CORS.
3. **Svaret läses ur tillståndet när körningen är klar** och kontrolleras med ett Zod-schema.
   Svarstextens `[1]` blir knappar till källorna. Ett svar som bryter mot schemat visas som ett fel
   med fältets namn.
4. **PDF:en visas med `react-pdf` (PDF.js) och sitt textlager.** Citatet söks i sidans text efter
   normalisering (blanksteg, bindestreck, citattecken, ligaturer och avstavning vid radslut) och
   markeras med `<mark>`. Hittas bara början eller slutet av citatet visas det med en förklaring.
5. **En mock av agenten** i `web/mock/` skickar samma AG-UI-händelser som `ag-ui-langgraph`, med en
   påhittad PDF. Webbläsartesterna i CI körs mot den, både mot en lokal build och mot Docker-imagarna.
6. **Docker-image med Next.js `standalone`.** Servern behöver inga `node_modules` i imagen. Basimagen
   finns för `linux/arm64` och `linux/amd64`.

## Konsekvenser

- Webbappen kan visas och testas utan backend, OpenAI-nyckel eller databas.
- Frågedialogen fungerar med båda formerna av interrupt som `ag-ui-langgraph` kan skicka, så
  backend kan välja.
- Kontraktet är skrivet en gång, i `web/src/lib/contract.ts`, och kontrolleras när appen kör. Ett
  fel i backend syns som ett tydligt fel i stället för en tom sida.
- CopilotKit är ett ramverk till att förstå. Det används bara genom fyra hooks, providern och
  chattkomponenten, så det mesta av gränssnittet är egna komponenter som går att läsa utan att
  känna CopilotKit.
- CopilotKit och AG-UI ändras ofta. Versionerna är låsta exakt, och en uppgradering kräver att
  webbläsartesterna går igenom.
- Svaren finns bara i webbläsaren och försvinner när sidan laddas om.

## Alternativ som valts bort

- **En enkel sida som FastAPI serverar.** Minst kod, men stegen live, dialogen och källpanelen hade
  krävt en egen klient för AG-UI. Det gör CopilotKit redan.
- **assistant-ui eller Vercel AI SDK.** Båda har chattkomponenter, men stödet för AG-UI och
  LangGraphs interrupt är inte lika direkt som i CopilotKit, där AG-UI kommer ifrån.
- **Webbläsarens inbyggda PDF-visare** (`#page=14` i en iframe). Den öppnar rätt sida men kan inte
  markera citatet, och den fungerar olika i olika webbläsare.
- **PDF.js hela visare** med dess sökfunktion. Den markerar sökträffar, men är en hel applikation
  att bädda in, och sökningen tål inte avstavning eller olika citattecken lika bra.
- **Vitest eller Jest för enhetstesterna.** Nodes egen testkörare räcker för rena funktioner och
  ger ett beroende mindre.
