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

  await card.getByTestId("ref-1").click();
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

for (const variant of ["standard", "legacy"] as const) {
  test(`asks which area is meant and continues with the answer (${variant} interrupt)`, async ({
    page,
  }) => {
    const suffix = variant === "legacy" ? " [legacy]" : "";
    await ask(page, `Vilken uppsägningstid gäller för ett kontrakt?${suffix}`);

    const dialog = page.getByTestId("clarify-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Vilket ramavtalsområde gäller frågan?");
    await dialog.getByRole("button", { name: "Programvaror och tjänster" }).click();
    await expect(dialog).toHaveCount(0);

    const card = page.getByTestId("answer-card");
    await expect(card).toHaveAttribute("data-status", "verified");
    await expect(card).toContainText("I ramavtalet för Programvaror och tjänster");
    await expect(page.getByTestId("agent-step")).toHaveCount(3);
  });
}

test("accepts an answer in the person's own words", async ({ page }) => {
  await ask(page, "Vilken uppsägningstid gäller för ett kontrakt?");
  const dialog = page.getByTestId("clarify-dialog");
  await dialog.getByLabel("Eller svara med egna ord").fill("Bemanningstjänster");
  await dialog.getByRole("button", { name: "Svara" }).click();
  await expect(page.getByTestId("answer-card")).toContainText(
    "I ramavtalet för Bemanningstjänster",
  );
});

test("shows a reservation and says when a quote is not on the page", async ({ page }) => {
  await ask(page, "Vilket vite gäller vid försenad leverans?");

  const card = page.getByTestId("answer-card");
  await expect(card).toHaveAttribute("data-status", "with_reservation");
  await expect(card).toContainText("Med reservation");
  await expect(card).toContainText("Citatet kunde inte kontrolleras mot avtalet");

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
