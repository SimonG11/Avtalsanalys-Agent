/**
 * The whole flow in the browser, against the mock agent (mock/scenarios.ts): a question, the
 * agent's steps, the answer card, the source panel with the highlighted quote, and the dialog
 * when the agent asks which framework area is meant.
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

  // Two tool calls are steps; the third, FinalAnswer, hands in the answer. It is not a step,
  // but a line that says the answer is being checked, until the answer is set.
  const check = page.getByTestId("answer-check");
  await expect(check).toHaveText("Kontrollerar svaret …");
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

  await panel.getByRole("button", { name: "Stäng källan" }).click();
  await expect(panel).toHaveCount(0);
});

// ag-ui-langgraph sends the older on_interrupt event, the standard outcome, or both.
const INTERRUPT_SHAPES = { both: "", legacy: " [legacy]", outcome: " [outcome]" };

for (const [shape, suffix] of Object.entries(INTERRUPT_SHAPES)) {
  test(`asks which area is meant and continues with the answer (${shape})`, async ({ page }) => {
    await ask(page, `Vilken uppsägningstid gäller för ett kontrakt?${suffix}`);

    const dialog = page.getByTestId("clarify-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Vilket ramavtalsområde gäller frågan?");
    await dialog.getByRole("button", { name: "Programvaror och tjänster" }).click();
    await expect(dialog).toHaveCount(0);

    const card = page.getByTestId("answer-card");
    await expect(card).toHaveAttribute("data-status", "verified");
    await expect(card).toContainText("I ramavtalet för Programvaror och tjänster");
    // The question is a step too, and it is done once the answer is back.
    const steps = page.getByTestId("agent-step");
    await expect(steps).toHaveCount(4);
    await expect(steps.nth(1)).toContainText("Fick svar");
    await expect(steps.nth(1)).toContainText("Vilket ramavtalsområde gäller frågan?");
    await expect(steps.nth(1)).toHaveAttribute("data-status", "complete");
  });
}

test("accepts an answer in the person's own words", async ({ page }) => {
  await ask(page, "Vilken uppsägningstid gäller för ett kontrakt?");
  const dialog = page.getByTestId("clarify-dialog");
  // The run waits for an answer, so Escape does not close the dialog.
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Eller svara med egna ord").fill("Bemanningstjänster");
  await dialog.getByRole("button", { name: "Svara" }).click();
  await expect(page.getByTestId("answer-card")).toContainText(
    "I ramavtalet för Bemanningstjänster",
  );
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
  // The check rejected the first draft; neither the draft nor the rejection is shown.
  await expect(page.getByText("Kontrollen underkände svaret")).toHaveCount(0);
  await expect(page.getByTestId("answer-check")).toHaveCount(0);

  await card.getByTestId("ref-2").click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toContainText("Kunde inte kontrolleras mot avtalstexten");
  await expect(page.getByTestId("match-note")).toHaveText("Citatet hittades inte i sidans text.");
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
  await expect(page.getByTestId("agent-step")).toHaveCount(3);
  await expect(page.getByTestId("agent-step").nth(2)).toHaveAttribute("data-status", "complete");
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
