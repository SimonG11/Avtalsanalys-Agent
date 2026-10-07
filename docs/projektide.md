# Fas 1 – Projektidé: Agentisk RAG för dokumentbearbetning

> Fas 1 så som den godkändes 2026-10-05, inlagd i repot i M12 utan ändringar. Datastrategin sist
> i dokumentet ändrade idén: systemet besvarar frågor om Statens inköpscentrals ramavtal i stället
> för användarens egna uppladdade avtal. Testsamlingen blev 30 frågor, och LegalBench-RAG har inte
> använts. Det som gäller står i [ADR:erna](adr/) och i [`docs/steg/`](steg/).

*Status: Kandidat A godkänd av Simon (2026-10-05). Datastrategi uppdaterad, se sist i dokumentet.*

## Kravbilden från caset

- Ett agentiskt ramverk (LangGraph eller nyare) som bearbetar dokument.
- Uppgiften ska passa en **agent bättre än ett fast workflow**: modellen ska själv bestämma vilka steg som behövs, i vilken ordning och när den är klar.
- Slutprodukten ska vara en professionell, skalbar agentisk RAG-app med verktyg och valideringar.

## Testet: agent eller workflow?

Ett workflow passar när stegen är kända i förväg (läs → extrahera → spara).
En agent behövs när **antalet steg och deras ordning beror på vad som står i dokumenten**:
svaret kräver flera hopp, dokument hänvisar till andra dokument, källor motsäger varandra
och systemet måste själv avgöra när beviset räcker. Alla tre kandidater nedan uppfyller det.

---

## Kandidat A (rekommenderas): Avtalsanalys-agent ("Contract Intelligence Agent")

**Vad den gör:** Användaren laddar upp en samling affärsavtal (huvudavtal, bilagor,
ändringsavtal, DPA:er, NDA:er) och ställer öppna frågor, till exempel:

- "Vilka av våra leverantörsavtal kan vi säga upp före 2027 utan vite?"
- "Uppfyller våra personuppgiftsbiträdesavtal vår GDPR-policy? Lista avvikelser med källa."
- "Vad gäller egentligen för ansvarsbegränsningen med Leverantör X efter alla ändringsavtal?"

Svaret är en rapport där varje påstående har en verifierad källhänvisning (dokument, paragraf, sida).

**Varför den kräver en agent och inte ett workflow:**

1. **Okänt antal hopp.** "Se bilaga 3", "enligt definitionen i §1.4", "ändringsavtal 2 ersätter §7" –
   agenten måste själv följa hänvisningar tills bilden är komplett.
2. **Konflikter mellan dokument.** Ett senare ändringsavtal kan upphäva en klausul. Agenten måste
   resonera om giltighet och datum, inte bara hämta den mest lika texten.
3. **Planering per fråga.** En uppsägningsfråga kräver datumräkning; en GDPR-fråga kräver jämförelse
   mot en policy-checklista. Agenten väljer verktyg och strategi själv.
4. **Självkorrigering.** Om en citatvalidering misslyckas eller beviset är tunt söker agenten igen
   med en ny strategi i stället för att hitta på.
5. **Människa i loopen.** Vid tvetydighet ("vilken leverantör X menar du?") eller hög risk frågar
   agenten användaren innan den fortsätter.

**Exempel på verktyg:** hybrid sökning (semantisk + nyckelord), läs specifik paragraf/sida,
följ korsreferens, lista dokument med metadata (part, datum, typ), datumräknare,
policy-jämförelse, citatverifierare.

**Exempel på valideringar:** varje påstående måste ha en källa som faktiskt innehåller texten,
strukturerad output med schema, kontroll av att senaste giltiga version använts, "jag vet inte"
när beviset saknas, guardrails mot promptinjektion i uppladdade dokument.

**Varför den är stark för caset:**

- **Mätbar.** Svensk egen testsamling plus det engelska benchmarket LegalBench-RAG gör att vi kan
  visa riktig utvärdering med siffror, inte bara en demo (se Datastrategi).
- **Affärsnära.** Juridik, inköp och compliance är ett tydligt och verkligt användningsfall.
- **Visar hela agentiska RAG-arkitekturen:** planering, verktygsanrop, flerstegssökning,
  reflektion, validering, human-in-the-loop och spårbarhet.

---

## Kandidat B: Upphandlingsagent (anbud enligt LOU)

Agenten läser ett förfrågningsunderlag, hittar alla ska-krav (ofta utspridda över flera bilagor),
matchar dem mot företagets egna dokument (CV:n, referensuppdrag, certifikat) och tar fram en
kravmatris med luckor och förslag på svar.

- **Plus:** mycket svenskt och konkret, tydligt affärsvärde.
- **Minus:** inget öppet annoterat dataset, så utvärderingen blir svagare. Mer "fyll i matris"
  än fritt resonerande, alltså närmare ett workflow.

## Kandidat C: Finansanalys-agent över årsredovisningar

Agenten svarar på analytikerfrågor över flera bolags årsredovisningar ("Hur har rörelsemarginalen
utvecklats för X jämfört med Y, och vad förklarar skillnaden enligt förvaltningsberättelsen?"),
med verktyg för tabellextraktion och beräkningar.

- **Plus:** tydliga multi-hop-frågor, finns benchmarks (t.ex. FinanceBench).
- **Minus:** tung tabell- och PDF-parsning tar tid från det agentiska, och domänen är vanlig i demos.

---

## Rekommendation

**Kandidat A, avtalsanalys-agenten.** Den har det tydligaste argumentet för en agent framför ett
workflow (korsreferenser, motstridiga versioner, frågeberoende strategi), den går att utvärdera
mot ett riktigt dataset, och den rymmer naturligt alla delar TokenTek vill se: verktyg,
valideringar och en skalbar agentisk RAG-arkitektur.

**Nästa steg efter godkännande:** Fas 2, arkitekturplanen.

---

## Datastrategi (uppdaterad 2026-10-05)

Simon påpekade att CUAD (2021) är gammalt och önskade en svensk motsvarighet.

**Slutsats från sökningen:** Det finns inget öppet, juristannoterat avtalsdataset på svenska.
De svenska LLM-benchmarks som finns gäller allmän- och medicinkunskap, inte avtal.
(Slutsatsen bygger på webbsökning, inte en uttömmande genomgång.)

**Förslag: två lager.**

1. **Svensk huvudkorpus: Statens inköpscentrals ramavtal (avropa.se). Vald av Simon 2026-10-05.**
   Riktiga, offentliga svenska avtal med huvuddokument, bilagor, allmänna villkor och ändringar.
   De hänvisar mycket till varandra ("enligt bilaga X", "punkt 12.3"), vilket är exakt det agenten
   ska kunna hantera. Vi bygger en egen svensk testsamling (cirka 50–100 frågor med handkontrollerade
   svar och källor). Att själv bygga och motivera testsamlingen är också en styrka i caset.
   Licens: allmänna handlingar, men villkoren för återanvändning ska bekräftas innan vi publicerar något.
   - **Facit för inläsningen:** huvudlistan "Alla giltiga ramavtal" (.xlsx, https://www.avropa.se/giltigaramavtal/excel)
     används för att kontrollera att varje PDF har lästs in korrekt, till exempel att ramavtalsnamn,
     referensnummer, leverantörer och giltighetstid som extraherats ur PDF:en stämmer med listan.
     Kolumnerna i listan är ännu inte verifierade.
   - Exempel: https://www.avropa.se/globalassets/bilagor/1.-aktuella-rao/it-drift2/it-drift-storre-23.3-10639-2023/1.-avropsstod/5.ramavtalets-huvuddokument.pdf

2. **Engelskt benchmark för jämförbara siffror: LegalBench-RAG (2024, CC BY 4.0).**
   6 889 frågor över 714 dokument, byggt ovanpå CUAD, MAUD, ContractNLI och PrivacyQA.
   Mäter just det RAG-delen ska göra: hitta exakt rätt textspann. Mini-varianten (776 frågor,
   72 dokument) räcker för caset. https://arxiv.org/abs/2408.10343

**Övriga nyare dataset (används inte som huvudkälla):**
- ContractEval (2025): LLM-utvärdering av klausulrisk, bygger på CUAD. https://arxiv.org/html/2508.03080
- ACORD (ACL 2025): klausulsökning för avtalsskrivning, 114 frågor, 126 000+ par. https://arxiv.org/abs/2501.06582
- Stanford Material Contracts Corpus (2025): över 1 miljon SEC-avtal 2000–2023, oannoterat. https://mcc.law.stanford.edu
