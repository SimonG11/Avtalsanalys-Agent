# ADR 0006: Registrets datamodell och inläsning

**Status:** Godkänt av Simon 2026-10-05 (PR #2). Punkt 1–3 justerades 2026-10-05 efter
inläsning av hela listan, före godkännandet.

## Kontext

Excel-listan "Alla giltiga ramavtal" har en rad per **leverantör och delområde**, inte per avtal.
Samma avtalsnummer står på flera rader (A Hub Group har 8 rader för ett avtal). Ett orgnr kan
ha flera leverantörsnamn (2Home Hotel Gävle och 2Home Sthlm South har samma orgnr), och namn kan
innehålla ett tidigare namn ("f.d. …"). Listan ändras när avtal tillkommer eller löper ut.

## Beslut

1. **Leverantören identifieras med normaliserat orgnr** (`NNNNNN-NNNN`), aldrig med namn. Namnen
   ligger i en egen tabell, `supplier_name`, med ett eventuellt tidigare namn. Utländska
   leverantörer (6 st i listan 2026-10-05) behåller sitt nummer som det står, t.ex. `FI01148912`.
2. **Avtalsnumret delas i diarienummer och löpnummer.** Diarienumret blir upphandlingen
   (`procurement`), som också används för att hitta PDF-paketen på avropa.se i M2. Några avtal
   har bara ett diarienummer och saknar löpnummer. En upphandling kan täcka flera
   ramavtalsområden, så området hör till delområdet och inte till upphandlingen.
3. **Datum hör till avtalet i ett delområde** (`agreement_sub_area`), inte till hela avtalet, eftersom
   samma avtal kan ha olika datum i olika delområden. Om två rader för samma avtal har olika orgnr gäller den
   första raden, och avvikelsen visas i inläsningsrapporten.
4. **Delområdet blir en hierarki** (`sub_area` med `parent_id`), en nod per nivå i " / "-strängen,
   separat per ramavtalsområde.
5. **Varje inläsning ersätter registertabellerna helt, i en transaktion.** `register_version`
   loggar vilken utgåva av listan som lästs in och när.
6. **Felaktiga rader stoppar inte inläsningen.** De listas med Excel-radnummer och orsak.

## Konsekvenser

- Samma fil två gånger ger samma tabeller (idempotent), och en misslyckad inläsning lämnar den
  förra utgåvan orörd.
- Avtal som försvunnit ur listan försvinner ur registret. Historik över tidigare utgåvor sparas
  inte i tabellerna, bara i de nedladdade filerna. Behövs historik senare kan tabellerna få en
  versionskolumn.
- Inläsningen tar bort och skriver om ca 4 500 rader, vilket tar under en sekund i Postgres.

## Alternativ som valts bort

- **En tabell som speglar Excel rad för rad.** Enkelt, men samma fakta (datum, leverantör) skulle
  upprepas på många rader och kunna motsäga varandra utan att det märks.
- **Uppdatera rad för rad (upsert) i stället för att ersätta.** Kräver logik för att hitta och ta
  bort avtal som försvunnit, utan att ge något vi behöver i dag.
