# ADR 0018: Ändringar efter granskningen: Kammarkollegiets meddelanden, fler ändringsord och frågans datum

**Status:** Godkänt (PR #20).
Bygger ut besluten 2 och 3 i [ADR 0017](0017-andringar.md) om hur steg 4 läser ändringar, och
R1q och R4q i [ADR 0009](0009-extraktion-avstamning-och-karantan.md). Resten av de besluten
gäller.

## Kontext

En granskning av PR #18 läste ändringarna som steg 4 märker (`replaces`) mot urvalets text och
fann tre fel som `find_amendments` och regeln om senaste lydelsen bygger vidare på:

- **Kammarkollegiets egna meddelanden lästes inte.** En frågelogg har 62 "Publikt
  informationsmeddelande", utan fråga och utan "Publikt svar". Steg 4 läste bara text efter
  "Publikt svar", så "Kammarkollegiet ersätter avsnitt 7.19.1.3 till följande skrivning"
  (39d8c1efe373) blev ingen ändring, och ett svar som citerade den gamla vitesklausulen gick
  igenom regeln.
- **Några ändringsord saknades.** "Tredje stycket i avsnitt 6.6 utgår." (bdf58b81d100 §120),
  "Tillägg till avsnitt 5.15 andra stycket:" (20ddb9ebf9cc §28) och "Kammarkollegiet justerar
  4.1.2, punkt 9" (20c753d88340 §70) blev ingen ändring. "utgår" räknades bara före "och", "ur"
  eller "i sin helhet", eftersom "utgår vite med 2 500 SEK" betyder att vite ska betalas.
- **En logg kan heta efter ett dokument som kom senare.** "Frågor och svar -
  Upphandlingsdokument" (20c753d88340) har frågorna från mars 2022, om Ansökningsinbjudan
  (2022-02-25); Upphandlingsdokumentet kom 2022-06-14. R1q slog ändå upp loggens nummer i filen
  som titeln anger, så två svar som ändrar 4.2.4 i Ansökningsinbjudan ledde till ett annat 4.2.4
  i Upphandlingsdokumentet, och regeln skulle ha krävt dem där.

Granskningen fann också att CLI:ns statusrad lovade mer än regeln prövar: "inget citerat avsnitt
har en ändring som svaret missar", fast tvetydiga ändringar och ändringar av en hel fil inte
prövas.

## Beslut

1. **Ett meddelande från Kammarkollegiet läses som ett svar.** I ett avsnitt med rubriken
   "Publikt informationsmeddelande" börjar Kammarkollegiets text där rubriken slutar, och nästa
   mening räknas som i ett svar.
2. **Fler ändringsord:** "utgår" sist i en mening, "Tillägg till" först i en mening (inte "ett
   tillägg till Kontraktet") och "justerar" ("justerar inte" är en negation, och "om … justerar"
   ett villkor). Ett nummer efter "justerar" läses som ett avsnittsnummer, som efter "enligt".
3. **En fråga gäller inget dokument som publicerades efter den.** R1q och R4q hoppar över filen
   som loggens titel anger när den publicerades efter frågans datum (den tidigaste stämpeln), och
   söker då bland sidans upphandlingsdokument, där frågans datum redan avgör
   (`settle_by_date`). Saknas numret, anges inte heller den filen som den som söktes.
4. **Statusraden säger vad regeln prövar:** "ändringar som säkert gäller ett citerat avsnitt är
   också citerade".

Mätt i körningen 2026-10-07 på pilotens 2 978 hänvisningar i ändringsdokument och frågeloggar:
60 med `replaces` i stället för 39 (16 i ändringsdokument, 27 i svar och 17 i meddelanden), och
alla 21 nya lästes och är ändringar. 28 hänvisningar i 20c753d88340 leder nu till
Ansökningsinbjudan. Andelen som reglerna löser går från 7 409 av 10 079 till 7 410 av 10 080
(73,51 procent); utfallen för testfrågorna q21–q23 är desamma
([steg 4](../steg/04-extraktion.md), "Ändringar").

## Konsekvenser

- `find_amendments` visar ändringar som Kammarkollegiet publicerade som meddelanden, bland dem
  två i q21:s upphandlingsdokument (5.15 och 6.8.3), och regeln kräver dem.
- En fråga utan tidsstämpel (36 avsnitt i loggarna) kan inte dateras, så filen som loggens titel
  anger gäller också när den kom senare. Svaret i 20c753d88340 §21 ändrar 4.2.4 bokstaven L i
  Ansökningsinbjudan men leder fortfarande till 4.2.4 i Upphandlingsdokumentet.
- Datumregeln gäller alla nummer och rubriker i loggarna, inte bara ändringar: frågor om en
  tidigare fas av upphandlingen leder till den fasens dokument.
- `process` och `index` måste köras igen (`run`) för att de nya ändringarna och målen ska
  skrivas.

## Alternativ som valts bort

- **Att datera en fråga utan stämpel efter frågan före eller efter den.** Loggarna står inte i
  datumordning: varje logg har frågor vars stämplar går bakåt jämfört med frågan före.
- **Att varje "utgår" är en ändring.** "utgår" inne i en mening är i urvalet ett vite eller en
  ersättning som ska betalas.
- **Att `find_amendments` sållar bort en ändring som är äldre än filen den ändrar.** Samma
  regel på två ställen, och den hjälper inte frågor utan stämpel; steg 4 löser det där målet
  väljs.
