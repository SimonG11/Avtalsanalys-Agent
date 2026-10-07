/**
 * The whole flow in the browser, against the mock agent (mock/scenarios.ts): a question, the
 * agent's thoughts and steps in the timeline, the answer, the source panel with the highlighted
 * quote, the agent's question in the chat, and readable text in both themes.
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function ask(page: Page, question: string): Promise<void> {
  const input = page.getByPlaceholder("Ställ en fråga om ramavtalen…");
  await input.fill(question);
  await input.press("Enter");
  // The input is cleared once the question has gone to the agent.
  await expect(input).toHaveValue("");
}

/** Opens a question's timeline, which folds into one line once the answer is there. */
async function openTimeline(page: Page, turn = 0): Promise<void> {
  const header = page.getByTestId("turn").nth(turn).getByTestId("timeline-header");
  if ((await header.getAttribute("aria-expanded")) !== "true") await header.click();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Avtalsanalys" })).toBeVisible();
  // The example questions appear when the chat has connected to the agent.
  await expect(page.getByRole("button", { name: "Uppsägningstid" })).toBeVisible();
});

test("starts a question from an example", async ({ page }) => {
  await page.getByRole("button", { name: "Uppsägning i IT-drift" }).click();
  await expect(page.getByTestId("answer-card")).toHaveAttribute("data-status", "verified");
});

test("answers with verified sources and opens the cited page with the quote marked", async ({
  page,
}) => {
  await ask(page, "Hur säger kunden upp ett kontrakt inom IT-drift?");

  // While the agent works, the timeline shows its thoughts and steps as they come. The third
  // tool call, FinalAnswer, hands in the answer: it is not a step but the check of the answer.
  const check = page.getByTestId("answer-check");
  await expect(check).toContainText("Kontrollerar svaret …");
  const thoughts = page.getByTestId("thought");
  await expect(thoughts).toHaveCount(2);
  await expect(thoughts.nth(0)).toContainText("Letar efter reglerna om uppsägning");
  await expect(thoughts.nth(0)).toContainText("Jag söker i de allmänna villkoren");
  await expect(thoughts.nth(0)).not.toContainText("**");
  const steps = page.getByTestId("agent-step");
  await expect(steps).toHaveCount(2);
  await expect(steps.nth(0)).toContainText("Sökte i dokumenten");
  await expect(steps.nth(0)).toContainText("uppsägningstid kontrakt");
  await expect(steps.nth(1)).toContainText("Läste avsnitt");
  await expect(steps.nth(1)).toHaveAttribute("data-status", "complete");

  const card = page.getByTestId("answer-card");
  await expect(card).toHaveAttribute("data-status", "verified");
  await expect(card).toContainText("Verifierat");
  await expect(card).toContainText("tre månaders uppsägningstid");
  await expect(check).toHaveCount(0);
  await expect(card.getByTestId("reservations")).toHaveCount(0);

  // With the answer there, the timeline folds into one line, and opens again on a click.
  const header = page.getByTestId("timeline-header");
  await expect(header).toHaveText(/^Arbetade i \d+ s · 3 steg$/);
  await expect(steps).toHaveCount(0);
  await header.click();
  await expect(steps).toHaveCount(2);
  await expect(page.getByTestId("draft")).toHaveText("Kontrollerade svaret");
  await expect(page.getByText(/Thought for|Thinking/)).toHaveCount(0);

  await card.getByTestId("ref-1").first().click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toContainText("Avsnitt 6.21.9 Uppsägning · sida 2");
  await expect(panel).toContainText("Kontrollerat mot avtalstexten");

  // The quote spans two lines of the PDF, so it is marked in two text items.
  const marks = panel.locator("mark.quote-mark");
  await expect(marks).toHaveCount(2);
  await expect(marks.first()).toContainText("Kunden har rätt att säga upp Kontraktet");
  await expect(marks.last()).toContainText("Uppsägningen ska vara skriftlig");
  await expect(page.getByTestId("match-note")).toHaveCount(0);
  await expect(page.getByTestId("page-note")).toHaveCount(0);

  await panel.getByRole("button", { name: "Stäng källan" }).click();
  await expect(panel).toHaveCount(0);
});

test("shows the date calculation in Swedish with its result", async ({ page }) => {
  await ask(
    page,
    "Kontraktet inom IT-drift ska upphöra 2027-02-17. När måste kunden säga upp det?",
  );

  await expect(page.getByTestId("answer-card")).toContainText("senast 2026-11-17");
  await openTimeline(page);
  const step = page.locator('[data-testid="agent-step"][data-tool="calculate_date"]');
  await expect(step).toHaveAttribute("data-status", "complete");
  await expect(step).toContainText("Räknade ut datum");
  await expect(step).toContainText("från: 2027-02-17");
  await expect(step).toContainText("enhet: månader");
  await expect(step).toContainText("riktning: före");
  await expect(step.getByTestId("step-outcome")).toHaveText(
    "2027-02-17 minus 3 månader = 2026-11-17 (tisdag)",
  );
  await expect(page.getByTestId("thought").nth(2)).toContainText(
    "Räknar ut sista dagen för uppsägning",
  );
});

// ag-ui-langgraph sends the older on_interrupt event, the standard outcome, or both.
const INTERRUPT_SHAPES = { both: "", legacy: " [legacy]", outcome: " [outcome]" };

for (const [shape, suffix] of Object.entries(INTERRUPT_SHAPES)) {
  test(`asks which area is meant and continues with the answer (${shape})`, async ({ page }) => {
    await ask(page, `Vilken uppsägningstid gäller för ett kontrakt?${suffix}`);

    // The question comes in the chat, under the steps that led to it, not in a dialog.
    const question = page.getByTestId("clarify-card");
    await expect(question).toBeVisible();
    await expect(question).toContainText("Vilket ramavtalsområde gäller frågan?");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("timeline-header")).toHaveText("Väntar på ditt svar · 2 steg");
    await expect(page.getByPlaceholder("Skriv ett eget svar…")).toBeVisible();
    await question.getByRole("button", { name: "Programvaror och tjänster" }).click();
    await expect(question).toHaveCount(0);

    const card = page.getByTestId("answer-card");
    await expect(card).toHaveAttribute("data-status", "verified");
    await expect(card).toContainText("I ramavtalet för Programvaror och tjänster");
    // The question is a step too, and it is done once the answer is back.
    await openTimeline(page);
    const steps = page.getByTestId("agent-step");
    await expect(steps).toHaveCount(4);
    await expect(steps.nth(1)).toContainText("Fick svar");
    await expect(steps.nth(1)).toContainText("Vilket ramavtalsområde gäller frågan?");
    await expect(steps.nth(1)).toContainText("Du svarade: Programvaror och tjänster");
    await expect(steps.nth(1)).toHaveAttribute("data-status", "complete");
  });
}

test("takes an answer in the person's own words from the chat field", async ({ page }) => {
  await ask(page, "Vilken uppsägningstid gäller för ett kontrakt?");
  await expect(page.getByTestId("clarify-card")).toBeVisible();

  const input = page.getByPlaceholder("Skriv ett eget svar…");
  await input.fill("Bemanningstjänster");
  await input.press("Enter");
  await expect(page.getByTestId("clarify-card")).toHaveCount(0);
  await expect(page.getByTestId("answer-card")).toContainText(
    "I ramavtalet för Bemanningstjänster",
  );
  // The answer went to the agent's question; it did not start a new question.
  await expect(page.getByTestId("question")).toHaveCount(1);
  await expect(page.getByPlaceholder("Ställ en fråga om ramavtalen…")).toBeVisible();
});

test("shows the reservations and says when a quote is not on the page", async ({ page }) => {
  await ask(page, "Vilket vite gäller vid försenad leverans?");

  const card = page.getByTestId("answer-card");
  await expect(card).toHaveAttribute("data-status", "with_reservation");
  await expect(card).toContainText("Med reservation");
  await expect(card).toContainText("Se reservationerna under svaret.");
  const reservations = card.getByTestId("reservations");
  await expect(reservations).toContainText("Reservationer");
  await expect(reservations.locator("li")).toHaveCount(2);
  await expect(reservations).toContainText("Citat 2 kunde inte kontrolleras mot avtalstexten.");
  await expect(card).toContainText("Citatet kunde inte kontrolleras mot avtalet");
  // The check sent the first draft back; the timeline shows that and why, in Swedish.
  await expect(page.getByTestId("answer-check")).toHaveCount(0);
  await openTimeline(page);
  const drafts = page.getByTestId("draft");
  await expect(drafts).toHaveCount(2);
  await expect(drafts.nth(0)).toHaveAttribute("data-outcome", "rejected");
  await expect(drafts.nth(0)).toContainText("Kontrollen skickade tillbaka utkastet");
  await expect(drafts.nth(0)).toContainText("citat 1 står inte i avsnittet");
  await expect(drafts.nth(1)).toHaveText("Kontrollerade svaret");
  await expect(page.getByText("Kontrollen underkände svaret")).toHaveCount(0);

  await card.getByTestId("ref-2").click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toContainText("Kunde inte kontrolleras mot avtalstexten");
  await expect(page.getByTestId("match-note")).toHaveText("Citatet hittades inte i sidans text.");
  await expect(page.getByTestId("page-note")).toHaveCount(0);
});

test("opens a quote further down a section on the page it is on", async ({ page }) => {
  await ask(page, "Vilket vite gäller vid försenad leverans?");

  // Source 3's section starts on page 3, the page the citation names; the quote is on page 4.
  await page.getByTestId("answer-card").getByTestId("ref-3").click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toContainText("Avsnitt 7.2 Vite vid försenad leverans · sida 3");
  await expect(page.getByTestId("page-note")).toHaveText(
    "Citatet står på sida 4. Avsnittet börjar på sida 3.",
  );
  const marks = panel.locator("mark.quote-mark");
  await expect(marks).toHaveCount(2);
  await expect(marks.first()).toContainText("Vitet ska betalas inom trettio (30) dagar");
  await expect(page.getByTestId("match-note")).toHaveCount(0);
});

test("keeps earlier answers when a new question is asked", async ({ page }) => {
  await ask(page, "Hur säger kunden upp ett kontrakt inom IT-drift?");
  await expect(page.getByTestId("answer-card")).toHaveCount(1);

  await ask(page, "Vad kostar en lunch?");
  const cards = page.getByTestId("answer-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toHaveAttribute("data-status", "verified");
  await expect(cards.nth(1)).toHaveAttribute("data-status", "no_answer");
  await expect(cards.nth(1)).toContainText("Inget svar");
});

test("does not show an earlier answer for a question whose run failed", async ({ page }) => {
  await ask(page, "Hur säger kunden upp ett kontrakt inom IT-drift?");
  await expect(page.getByTestId("answer-card")).toHaveCount(1);

  await ask(page, "Vad gäller för underleverantörer? [fel]");
  // The failed question has no answer, so its timeline stays open.
  const steps = page.getByTestId("turn").nth(1).getByTestId("agent-step");
  await expect(steps).toHaveCount(1);
  await expect(steps.nth(0)).toHaveAttribute("data-status", "complete");
  // The failed run gets an error, not a card, and not the first question's card.
  await expect(page.getByTestId("run-failed")).toBeVisible();
  await expect(page.getByTestId("answer-card")).toHaveCount(1);
});

test("shows a source in a Word file without a page and without a PDF", async ({ page }) => {
  await ask(page, "Vilken säkerhetsnivå gäller? Står det i en bilaga?");

  const card = page.getByTestId("answer-card");
  await expect(card).toHaveAttribute("data-status", "no_answer");
  await expect(card).toContainText("Källorna visar var frågan regleras");
  // The answer is plain text; its paragraphs and list lines keep their line breaks.
  await expect(card.locator("p").first()).toHaveCSS("white-space", "pre-line");
  await expect(card.getByTestId("source-1")).toHaveText(
    /Exempelbilaga Avropsförfrågan \(fiktiv\), avsnitt Avropsbilaga”Kunden anger/,
  );

  await card.getByTestId("source-1").click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toContainText("Avsnitt Avropsbilaga");
  await expect(panel).not.toContainText("null");
  await expect(page.getByTestId("no-pdf")).toBeVisible();
});

test("answers from the register with one line per agreement", async ({ page }) => {
  await ask(page, "Vilket avtalsnummer har IT-drift?");

  const card = page.getByTestId("answer-card");
  await expect(card).toHaveAttribute("data-status", "verified");
  await expect(card).toContainText("Uppgifterna stämmer med registret.");
  await expect(card.getByTestId("source-1")).toHaveCount(0);

  // Three rows, two agreements: the register writes the first one's number two ways.
  const register = card.getByTestId("register-facts");
  await expect(register).toContainText("Ur registret: 2 avtal");
  const agreements = register.getByTestId("register-agreement");
  await expect(agreements).toHaveCount(2);
  await expect(agreements.nth(0)).toContainText("00.0-0000-2026-001");
  await expect(agreements.nth(0)).toContainText("tidigare Gamla Exempelbolaget AB (fiktivt)");
  await expect(agreements.nth(0)).toContainText("2 delområden");
  await expect(agreements.nth(0)).toContainText("längst till 2030-12-31");
  await expect(agreements.nth(1)).toContainText("Testleverantören AB (fiktiv) (000000-0002)");
  await expect(agreements.nth(1)).not.toContainText("längst till");
});

test("keeps the text readable in the dark theme and lets the person switch theme", async ({
  page,
}) => {
  // Without animations, so the colours are measured after a theme switch, not halfway through.
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await ask(page, "Vilket vite gäller vid försenad leverans?");
  const card = page.getByTestId("answer-card");
  await expect(card).toHaveAttribute("data-status", "with_reservation");
  await openTimeline(page);
  await card.getByTestId("ref-3").click();
  await expect(page.getByTestId("source-panel").locator("mark.quote-mark")).toHaveCount(2);

  // Every visible text element has a contrast of at least 4.5:1 against what is behind it.
  const worst = await page.evaluate(lowestContrast);
  expect(worst.ratio, `${worst.text} (${worst.color} on ${worst.background})`).toBeGreaterThan(4.5);

  // The header's button switches to the light theme, and the page follows.
  await page.getByRole("button", { name: "Ljust tema" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const light = await page.evaluate(lowestContrast);
  expect(light.ratio, `${light.text} (${light.color} on ${light.background})`).toBeGreaterThan(4.5);
  await expect(page.getByRole("button", { name: "Mörkt tema" })).toBeVisible();
});

/**
 * Runs in the page: the lowest contrast between a visible text element's colour and the first
 * opaque background behind it. The PDF page and its marks are left out (they are paper).
 */
function lowestContrast() {
  const parse = (value: string) => (value.match(/[\d.]+/g) ?? []).map(Number);
  const luminance = ([r, g, b]: number[]) => {
    const channel = (c: number) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  };
  const backgroundOf = (element: Element | null): string => {
    for (let node = element; node; node = node.parentElement) {
      const background = getComputedStyle(node).backgroundColor;
      const [, , , alpha = 1] = parse(background);
      if (alpha > 0.9) return background;
    }
    return "rgb(255, 255, 255)";
  };
  let worst = { ratio: Infinity, text: "", color: "", background: "" };
  for (const element of document.querySelectorAll("body *")) {
    if (element.closest(".react-pdf__Page, svg")) continue;
    const own = [...element.childNodes].some(
      (node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim(),
    );
    if (!own || !(element as HTMLElement).offsetParent) continue;
    const style = getComputedStyle(element);
    const [, , , alpha = 1] = parse(style.color);
    if (alpha < 0.5) continue;
    const background = backgroundOf(element);
    const a = luminance(parse(style.color));
    const b = luminance(parse(background));
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (ratio < worst.ratio) {
      worst = {
        ratio,
        text: element.textContent?.trim().slice(0, 40) ?? "",
        color: style.color,
        background,
      };
    }
  }
  return worst;
}
