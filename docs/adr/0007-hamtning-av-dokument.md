# ADR 0007: Hämtning av dokumenten från avropa.se

**Status:** Förslag (M2), granskas av Simon i PR:en

## Kontext

Avtalsdokumenten finns bara som länkar på avropa.se. Det finns inget API. Varje ramavtalsområde
har en sida (101 sidor i A–Ö-listan 2026-10-05) med:

- en faktaruta där "Ramavtalsnummer" är samma diarienummer som i Excel-registret,
- dokument för hela området under rubriker som "Avtal", "Upphandling" och "Stöddokument och länkar",
- ett kort per leverantör med "Avtal: <avtalsnummer>", ibland med leverantörens eget ramavtal som PDF.

De flesta länkar går till `/globalassets/...fil.pdf?v=<version>`. Versionen byts när filen byts.
Några (Microsofts och IBM:s volymavtal) går till `/contentassets/...` utan version, men servern
svarar med en ETag.

## Beslut

1. **Sidorna kopplas till registret med diarienumret**, inte med sidans namn eller URL. Saknar
   sidan faktan "Ramavtalsnummer" används diarienumret i leverantörskortens avtalsnummer.
2. **Leverantörsdokument får sitt avtalsnummer** från kortet de ligger i. Då kan M4 stämma av
   ett leverantörsavtal mot rätt rad i registret.
3. **Filerna sparas under sin SHA-256-hash** (`data/documents/<hash>.pdf`). Samma fil som länkas
   från flera sidor sparas en gång, och en fil skrivs aldrig över. En ny version blir en ny fil
   bredvid den gamla.
4. **En omkörning hämtar bara det som ändrats.** Samma `?v=` som förra gången ger ingen
   förfrågan alls. Länkar utan version frågas med förra ETagen (`If-None-Match`), och sajten
   svarar 304 om filen är densamma.
5. **Katalogen ligger i Postgres** (`agreement_page`, `source_document`, `agreement_page_document`)
   utan främmande nycklar till registret. Registret ersätts vid varje inläsning medan dokumenten
   ligger kvar, så de kopplas på diarie- och avtalsnummer.
6. **Ett felaktigt dokument stoppar inte körningen.** Det rapporteras med orsak, och svar som inte
   är en PDF eller Word-fil (t.ex. en felsida) sparas aldrig.
7. **Vi belastar sajten som en person som surfar:** en förfrågan i taget, en halv sekund mellan
   förfrågningarna, och programmet anger vem det är i `User-Agent`. `robots.txt` stänger inte
   sidorna vi läser.

## Konsekvenser

- Varje körning läser alla 101 sidor (ca 1 minut med pausen) för att se nya och ändrade länkar.
  Själva dokumenten hämtas bara om de är nya eller ändrade.
- Gamla versioner av en fil ligger kvar på disken och i hashen, men katalogen pekar på den
  senaste. Behövs historik över versioner kan `source_document` få en versionstabell.
- Ändrar avropa.se sidans HTML måste `ingestion/avropa_pages.py` ändras. Testerna körs mot sparade
  riktiga sidor, så en ändring syns som ett fel i en enda modul.

## Alternativ som valts bort

- **Gissa dokumentens adresser från diarienumret.** Mapparna under `/globalassets/` följer inget
  fast mönster (t.ex. `itk-2020/itk-2.-ledning-och-it-projekt/`), så det skulle missa dokument.
- **Spara filerna under sitt namn per upphandling.** Lättare att bläddra i, men samma mall
  (t.ex. säkerhetsskyddsavtalen) skulle ligga i många mappar, och en ny version skulle skriva
  över den gamla.
- **Hämta allt varje gång och jämföra hashen.** Enkelt, men ca 130 MB per körning för urvalet
  och mycket mer för alla områden, utan att ge något mer än versionen och ETagen redan ger.
