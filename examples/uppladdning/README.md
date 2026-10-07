# Exempelavtal för uppladdning

`exempelavtal-it-konsult.md` är ett **påhittat** avropsavtal mellan Exempelkommunen och
Konsultbolaget Exempel AB, gjort för att visa hur agenten jämför en egen fil med ramavtalen
([ADR 0026](../../docs/adr/0026-egna-filer.md)). Parterna, avtalsnumret och alla belopp är
påhittade. Avtalet säger själv att det är ett avrop från IT-konsulttjänster, delområde 1
Verksamhetens IT-behov (23.3-1688-2024), och att ramavtalets allmänna villkor gäller i övrigt.

Ramavtalets text som avtalet jämförs med är **Allmänna villkor** för IT-konsulttjänster 1.
Verksamhetens IT-behov (2025-08-19, sha256 `ab7277a9c123…`), som den finns i pilotens data.

## Klausuler som avviker

| Exempelavtalet | Vad det säger | Allmänna villkor | Vad ramavtalet säger |
|---|---|---|---|
| 3 Pris och ersättning | Resor till och från stationeringsorten ersätts med faktisk kostnad, restid med halva timpriset | 2.9.1 Priser och ersättning | Ersättning utgår inte för resa till och från stationeringsorten; restidsersättning bara om annat överenskommits |
| 4 Prisjustering | Leverantören får begära prisjustering enligt AKI en gång per år | 2.9.2 Prisjustering | Ramavtalsleverantören har inte rätt att begära prisjustering i Kontrakt; prisjustering hanteras av Avropsberättigad (indexet, AKI, och basmånaden är desamma) |
| 5 Fakturering och betalning | Faktureringsavgift 45 kronor per faktura | 2.11.1 Fakturering | Faktureringsavgift eller liknande accepteras inte |
| 6 Vite vid försening | 1 000 kronor per Konsult och påbörjad Arbetsdag, högst 10 000 kronor | 2.19.1.3 Vite vid Försening | 2 500 SEK per Konsult och påbörjad Arbetsdag, högst 25 000 SEK; gäller om inte annat framgår av Kontraktet, så avropet får ändra det |
| 7 Skadestånd och ansvarsbegränsning | Leverantörens ansvar högst 10 procent av kontraktets värde under hela tiden, också vid grov oaktsamhet | 2.19.8 Skadestånd och ansvarsbegränsningar | Högst 50 procent av medelvärdet av kontraktets värde per kontraktsår, och begränsningen gäller inte vid uppsåt eller grov oaktsamhet; taket får ändras i en förnyad konkurrensutsättning, undantaget för grov oaktsamhet inte |

## Klausuler som stämmer

| Exempelavtalet | Allmänna villkor |
|---|---|
| 2 Uppdrag och kontraktstid: löpande räkning under Arbetsdag | 2.9.1 Priser och ersättning |
| 5 Fakturering och betalning: månadsvis i efterskott, Peppol BIS Billing, betalning 30 kalenderdagar efter mottagen korrekt faktura | 2.11.1 Fakturering och 2.11.3 Betalning |
| 8 Uppsägning: utan skäl, 14 kalenderdagar för upp till fyra konsulter, 21 för fem eller fler | 2.19.10 Avropsberättigad förtida uppsägning av Kontrakt |
| 9 Tillämplig lag och tvister: svensk rätt, allmän domstol där Kunden har sitt säte | 2.22 Tillämplig lag och tvistelösning |

Vite och ansvarsbegränsning är bra exempel på det agenten ska se: ramavtalet tillåter att
avropet ändrar vitet och ansvarstaket, men inte att begränsningen gäller vid grov oaktsamhet.

## Som PDF

Demot laddar upp avtalet som PDF. PDF:en byggs, den checkas aldrig in:

```bash
uv run python examples/uppladdning/render_pdf.py /tmp/exempelavtal-it-konsult.pdf
```

Skriptet vägrar skriva inuti repot. Markdown-filen går också att ladda upp som den är, och i
terminalen bifogas den med `--fil`:

```bash
uv run python -m avtalsagent.agent --fil examples/uppladdning/exempelavtal-it-konsult.md \
  "Jämför mitt avtal med ramavtalets allmänna villkor. Vad avviker?"
```
