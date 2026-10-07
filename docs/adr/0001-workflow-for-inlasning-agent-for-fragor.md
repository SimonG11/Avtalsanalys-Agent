# ADR 0001: Workflow för inläsning, agent för frågor

**Status:** Godkänt av Simon 2026-10-05 (arkitekturplanen, fas 2). Av de tre ramarna under
Konsekvenser byggdes bara den sista, högst två valideringsvarv: högst två nya försök efter ett
underkänt svar ([ADR 0015](0015-valideringskedjan.md)). En gräns för antalet verktygsanrop och en
tidsgräns per nod byggdes inte. I stället finns en gräns på 16 modellanrop per körning, nya
försök inräknade ([ADR 0013](0013-agenten.md)), och granskarens anrop ges upp efter 60 sekunder
och görs om högst två gånger (`agent/reviewer.py`).

## Kontext

Systemet gör två olika saker. Det läser in ramavtal från avropa.se (hämta, tolka PDF,
dela upp, stämma av mot Excel-registret, indexera), och det besvarar öppna frågor om avtalen.
Uppgiften i caset är att hitta något där en agent med verklig frihet passar bättre än ett
fast workflow. Det betyder inte att allt ska vara agent.

## Beslut

- **Inläsningen är ett workflow.** Stegen är alltid desamma och i samma ordning. Varje steg är
  en egen funktion med tydlig in- och utdata.
- **Frågebesvarandet är en agent.** Hur många steg, vilka dokument och vilka verktyg som behövs
  beror på frågan och på vad dokumenten säger, till exempel en hänvisning till en bilaga eller
  ett ändringsdokument som ersätter en klausul. Det kan inte bestämmas i förväg.
- **Valideringen av svaret ligger utanför agenten**, som deterministisk kod och en separat
  granskare. Agenten ska inte godkänna sitt eget arbete.

## Konsekvenser

- Inläsningen blir förutsägbar, billig och lätt att felsöka. Excel-avstämningen ger ett objektivt
  mått på om den fungerar.
- Agentens frihet finns där den gör nytta, med tydliga ramar: max antal verktygsanrop, timeout
  per nod och max två valideringsvarv.
- Två olika sätt att bygga på finns i samma kodbas. Gränsen mellan dem måste hållas tydlig
  (mapparna `ingestion/` och `agent/`).

## Alternativ som valts bort

- **Agent även för inläsningen.** Ger inget när stegen alltid är desamma, och gör inläsningen
  dyrare och svårare att återskapa.
- **Workflow även för frågorna** (fast RAG-kedja: sök, hämta, svara). Klarar inte frågor som kräver
  flera steg, till exempel att följa en hänvisning eller avgöra vilken lydelse som gäller.
