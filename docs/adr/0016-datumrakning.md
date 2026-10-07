# ADR 0016: `calculate_date`: datumräkning med svenska helgdagar, och registerregeln räknar om steget

**Status:** Godkänt av Simon 2026-10-07 (PR #16, till main med PR #17).
Lägger till ett sjunde verktyg i avtal-mcp ([ADR 0012](0012-avtal-mcp.md)) och bygger ut
registerregeln i [ADR 0015](0015-valideringskedjan.md), punkt 3. Resten av de besluten gäller.

## Kontext

Arkitekturplanen har verktyget `berakna_datum`: "Deterministisk datumräkning (uppsägningstid,
förlängning) – LLM:er räknar dåligt". M6 byggde de sex andra verktygen först, för MVP:n.

- **Avtalen räknar i flera enheter.** I pilotens 205 filer står "N Arbetsdagar" 449 gånger (73
  filer), "N kalenderdagar" 485 gånger (70 filer) och "N månader" 882 gånger (93 filer). Avtalen
  definierar Arbetsdag som "en i Sverige helgfri måndag till fredag 08.00-17.00". Tio arbetsdagar
  före en dag i början av april eller i slutet av december blir fel med vanlig kalenderräkning.
- **Registerregeln i M8 räknade själv, men för grovt.** Den godtog ett datum som låg högst en dag
  från ett belagt datum flyttat med en förskjutning som meningen skrev ("tre månader före
  2027-02-17"). Den läste inte "arbetsdagar" eller "kalenderdagar", kunde inte pröva en uträkning
  som byggde på en annan, och underkände ett rätt datum som låg en dag från registrets ("dagen
  innan avtalet löper ut").
- **Klienten skickar historiken** (webbappens kontrakt, punkt 7). Kontrollen kan inte lita på ett
  verktygssvar i den, av samma skäl som för citaten och registerraderna (ADR 0015).
- **Midsommarafton, julafton och nyårsafton** är inga allmänna helgdagar enligt lagen (1989:253),
  men många behandlar dem som helgdagar, och lagen (1930:173) gör det för frister som lagen sätter.
  Tio arbetsdagar före 2026-12-28 blir 2026-12-11 eller 2026-12-10 beroende på hur julafton räknas.

## Beslut

1. **`calculate_date` är avtal-mcp:s sjunde verktyg**, efter `search_register`, som planen säger
   (ADR 0003: alla dataverktyg i avtal-mcp, bara `ask_user` i grafen). Det läser ingen databas.
   `server._with_session` tar därför emot ett verktyg utan `session`, och anropar det utan att
   öppna en session; `session` eller `embedder` någon annanstans i signaturen är ett fel.
2. **Argument:** `start` (ett datum 2005–2100), `amount` (1–3650), `unit` (`days`,
   `working_days`, `weeks`, `months`, `years`), `direction` (`after` eller `before`) och
   `include_start`. **Svar:** `result`, `weekday`, `step` (uträkningen på en rad, till exempel
   "2027-02-17 minus 3 månader = 2026-11-17"), `skipped` (helgdagar på vardagar som inte räknades
   som arbetsdagar) och `notes`. Före 2005 var annandag pingst en helgdag och nationaldagen inte
   (SFS 2004:1072), så ett start- eller slutdatum utanför 2005–2100 är ett fel
   (`ArgumentError`).
3. **Arbetsdagar** är måndag–fredag utom de allmänna helgdagarna enligt lagen (1989:253). De räknas
   ut i koden: påsk med Meeus/Jones/Butchers algoritm, och de rörliga helgdagarna från den.
   Startdagen räknas inte, så en arbetsdag efter en fredag är måndagen. Aftnarna räknas som
   arbetsdagar, eftersom avtalens definition ("helgfri") läst ordagrant bara undantar helgdagar.
   När det ändrar svaret säger en not vilket datum det blir om de räknas som helgdagar, och vilka
   aftnar det beror på, så att agenten och användaren ser skillnaden.
4. **Månader** behåller dagen i månaden, eller tar månadens sista dag när månaden är kortare
   (2026-01-31 plus en månad är 2026-02-28). Ett år är tolv månader. Med `include_start` hör
   startdagen till perioden, och resultatet flyttas en dag mot startdatumet: 48 månader från och
   med 2024-11-14 slutar 2028-11-13, som registrets datum. Ett resultat i dagar, veckor, månader
   eller år som hamnar på en helg flyttas inte; en not säger vilken dag det är.
5. **En gemensam modul, `domain/dates.py`,** används av verktyget och av registerregeln. Regeln
   läser steget som verktyget skriver ("ÅÅÅÅ-MM-DD plus|minus N enhet = ÅÅÅÅ-MM-DD", och fler steg
   efter ett kommatecken) och räknar om det:
   - Ett rätt steg från ett belagt datum, eller från ett tidigare rätt stegs resultat, belägger
     sitt resultat. Det gäller också ett datum en dag från registrets, som annars är ett fel.
   - Ett fel steg är ett problem som säger vilket datum det blir, också när datumet i sig är
     belagt.
   - Ett steg från ett datum som inget belägger räknas om, men belägger inget. Ett datum som
     regeln godtar som framräknat i löptext räknas som belagt.
   - Ett steg efter ett kommatecken räknas från resultatet före, som verktyget skriver det, eller
     från kedjans första datum, när svaret räknar upp flera datum från ett.
   - Ett steg i arbetsdagar är rätt både med aftnarna som arbetsdagar och som helgdagar, och
     belägger båda datumen: det andra är det som noten ger.
   - Stegets eget antal ("3 månader") är ingen förskjutning för datumen runt omkring: ett datum
     bredvid steget måste vara dess resultat, inte en dag ifrån.
   - I löptext läser regeln nu också arbetsdagar, kalenderdagar och räkneord upp till 99. En
     arbetsdag fel godtas där, som en dag fel för kalenderdagar.
6. **Prompten** säger åt agenten att aldrig räkna själv, att skriva `step` ordagrant i samma mening
   som datumet, och att läsa avtalets definition av Arbetsdag först. **Granskarens prompt** säger
   att en skriven uträkning redan är omräknad i koden, så att granskaren bara prövar startdatum,
   antal och enhet mot källorna, och att aftnarna är arbetsdagar om inte källan säger annat.

## Konsekvenser

- Ett framräknat datum syns med sin uträkning i svaret och kontrolleras exakt, utan modell.
  Granskaren behöver inte längre räkna.
- Ett framräknat datum kostar ett verktygsanrop till.
- Ett rätt steg belägger sitt datum i hela svaret. Påstår en annan mening något annat om samma
  datum, är det granskarens sak.
- Aftnarna är en tolkningsfråga som verktyget inte avgör. Noten gör den synlig, och regeln godtar
  båda datumen.
- Ett datum i frågan utan årtal ("15 mars") belägger inget, så ett steg från det underkänns.
  Agenten får skriva datumet med årtal efter att ha frågat användaren.
- Ett datum på en helgdag flyttas inte. Avtalen säger sällan vad som gäller då, och agenten ska
  säga det om avtalet gör det.
- Timmar ("inom 36 timmar") stöds inte.
- Webbappen får ett nytt verktyg och fem nya argumentnamn (kontraktets punkt 5).

## Alternativ som valts bort

- **Biblioteket `holidays` eller `workalendar`.** Ett nytt beroende för tretton helgdagar.
  Uträkningen är ett trettiotal rader och testas mot almanackan.
- **Att kontrollen läser verktygets svar ur historiken.** Klienten kan förfalska det.
- **Att kontrollen anropar `calculate_date` genom MCP.** Ett anrop över nätet för en ren funktion,
  som kontrollen kan köra själv.
- **Verktyget i agentens graf.** ADR 0003 håller alla dataverktyg i avtal-mcp.
- **En `session`-parameter som verktyget inte använder.** Det hade inte krävt någon ändring i
  servern, men hade sagt fel sak om verktyget.
- **Arbetsdagar som en egen flagga.** "working_days med months" vore meningslöst; enheten säger
  det.
- **Att räkna aftnarna som helgdagar.** Avtalens definition säger "helgfri", och lagen gör dem inte
  till helgdagar. Noten ger det andra datumet.
