# ADR 0022: Demot: fem frågor, fyra ur testsamlingen, körda mot den riktiga databasen, och README:n som ingång

**Status:** Föreslaget (M12).

## Kontext

M12 avslutar implementationsplanen: hela stacken i Docker Compose, en README, en genomgång av
arkitekturen och ett demoskript med fem frågor som visar agentens styrkor. Klart när en ny
utvecklare kan starta allt med `docker compose up` och köra demot.

Det som styr besluten:

- **Casets bedömning.** Det som bedöms är hur uppgiften är löst och hur väl systemet är förstått:
  valen, hur systemet fungerar och hur det är kontrollerat att det gör det som avsågs. Demot och
  README:n ska svara på de tre frågorna, inte bara visa att något svarar.
- **Varför en agent.** Uppgiften ska passa en agent bättre än ett workflow (ADR 0001). En fråga
  som alltid besvaras med samma sökning visar inte det. I q10 gör agenten ett enda anrop, men det
  som visas är att den själv väljer registret.
- **Svaren varierar mellan körningar.** Agenten väljer verktyg själv, så samma fråga kan ta olika
  vägar och ibland få en annan status. En demofråga som bara har körts en gång kan falla live.
- **Tidigare mätningar gjordes mot en ersättare för avtal-mcp** utan Postgres (steg 7, 8 och 11).
  Ingen fråga hade ställts till webbappen mot den riktiga databasen.

## Beslut

1. **Fem demofrågor, en per sak agenten gör som ett workflow inte gör:** frågar när frågan är oklar
   (en öppnare variant av q04), väljer registret i stället för dokumenten (q10), följer hänvisningar
   i flera steg (q14), hittar en rättelse som ersätter klausulen (q21) och säger "framgår inte"
   (q27). En reservfråga jämför leverantörer (q24).
2. **Fyra av frågorna kommer ur testsamlingen**, så att de har ett facit och en mätning bakom sig.
   Demot visar då samma sak som mätningen, och ett fel live går att jämföra med facit. Den femte är
   en öppnare variant av q04, eftersom ingen testfråga fick agenten att fråga i mätningen.
3. **Demot körs mot den riktiga databasen innan det visas**, i terminalen och i webbappen, och
   resultaten skrivs i steg 12 med tid, verktyg, status och källor. Mätningen av alla 30 frågor
   körs också mot den riktiga databasen, så att siffrorna i README:n är systemets.
4. **Demoskriptet säger vad som görs när något går fel:** för varje fråga vad som kan variera och vad
   det betyder, och fem nivåer från webbappen live till skärmbilder (`docs/demo.md`).
5. **Frågor som undviks live:** listor över många leverantörer, områden utanför piloten,
   inskannade dokument och frågor vars svar beror på dagens datum.
6. **README:n är ingången för den som granskar.** Efter hur systemet startas kommer uppgiften och
   varför den passar en agent, sedan arkitekturen som den är byggd, var agenten bestämmer och var koden bestämmer,
   kontrollerna i sex lager med siffror, arbetssättet och de kända begränsningarna. Detaljerna står
   i `docs/steg/` och `docs/adr/`, som README:n länkar till.
7. **Faserna 1 och 4 läggs i repot** som `docs/projektide.md` och `docs/implementationsplan.md`,
   så som de godkändes, bredvid faserna 2 och 3 (`docs/arkitektur.md`, `docs/validering.md`).
   Senare beslut står i ADR:erna; kopiorna ändras inte i efterhand.

## Konsekvenser

- Demot visar agentens styrkor med fyra mätta frågor och en variant av q04, och det som varierar
  mellan körningar är beskrivet i förväg.
- Demokörningen i webbappen hittade ett fel som inte beror på databasen men som ingen tidigare
  mätning kunde se: i webbappens väg begränsade AG-UI-adaptern körningen till 25 steg i grafen, så
  en fråga med fler än ungefär sex modellanrop föll (steg 12). Terminalen och mätningen anropar
  grafen direkt och påverkades inte. Felet är rättat i PR #22, där `make_agui_agent` ger adaptern
  grafens egen gräns och ett test kör en fråga med nio modellanrop genom `POST /agui`.
- Fyra av demofrågorna är bland de 30 testfrågorna. De visar inte hur systemet beter sig på frågor som ingen har skrivit
  facit till.
- Siffrorna i README:n gäller en körning en dag. En ny körning kan ge en fråga mer eller mindre rätt.

## Alternativ som valts bort

- **Fler demofrågor utanför testsamlingen.** Fråga 1 är den enda, eftersom ingen testfråga fick
  agenten att fråga. Fler sådana kunde visa mer, men har inget facit och ingen mätning bakom sig.
- **Bara en inspelad demo.** Säkrare, men visar inte att systemet fungerar nu. Inspelningen är i
  stället en reservnivå.
- **Listfrågor i demot** (alla leverantörer i ett område): listor inom ett delområde eller en
  region fungerar sedan filtret på delområde (q11 och q12 blev rätt), en lista över ett helt område
  är inte mätt, och svaren är långa och kontrollen tar tid.
- **En kort README som bara länkar vidare.** Den som granskar ska kunna se valen och kontrollerna
  utan att först läsa tretton stegfiler.
