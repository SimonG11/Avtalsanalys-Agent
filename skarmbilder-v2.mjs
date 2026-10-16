// Screenshots of the web app on main (the chat UI from PR #33, uploads from #35/#36, ask-backs
// as buttons and reasoning from #38) for the presentation deck.
//
// Usage, from the repo root with the app running (docker compose up):
//   node skarmbilder-v2.mjs web/node_modules/@playwright/test/index.mjs skarmbilder [own file]
//   BASE_URL   the web app (default http://localhost:3000)
//   own file   the contract for demo step F (default: the demo PDF in the project folder, else
//              examples/uppladdning/exempelavtal-it-konsult.md in the repo)
// On a Mac, install the browser once first: cd web && npm ci && npx playwright install chromium
//
// Each question runs in a fresh tab, as docs/demo.md says. A question that fails is logged and
// skipped, so one slow answer does not cost the other screenshots. The log goes to korning.log.
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , pwModule, outDir, ownFile] = process.argv;
const { chromium, expect } = await import(pathToFileURL(pwModule).href);
mkdirSync(outDir, { recursive: true });

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const DEMO_PDF = "/mnt/project-files/case-tokentek/implementering/demodata/exempelavtal-it-konsult.pdf";
const FILE =
  ownFile ?? (existsSync(DEMO_PDF) ? DEMO_PDF : "examples/uppladdning/exempelavtal-it-konsult.md");
const ANSWER = { timeout: 300_000 }; // the long upload comparison took 4 min 19 s
const T = { timeout: 60_000 };

function log(message) {
  const line = `${new Date().toISOString()} ${message}`;
  console.log(line);
  appendFileSync(`${outDir}/korning.log`, line + "\n");
}

// The cloud sandbox has its own Chromium; elsewhere Playwright uses the one it installed.
const SANDBOX_CHROMIUM = "/opt/pw-browsers/chromium";
const browser = await chromium.launch(
  existsSync(SANDBOX_CHROMIUM) ? { executablePath: SANDBOX_CHROMIUM } : {},
);

async function fresh(colorScheme = "light") {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    locale: "sv-SE",
    colorScheme,
  });
  const page = await context.newPage();
  await page.goto(BASE);
  // Hide the Next.js dev-mode indicator, which only exists under `next dev`.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  await expect(page.getByRole("heading", { name: "Avtalsanalys" })).toBeVisible(T);
  await expect(page.getByRole("button", { name: "Uppsägningstid" })).toBeVisible(T);
  return page;
}

async function ask(page, question) {
  const input = page.getByPlaceholder("Ställ en fråga om ramavtalen…");
  await input.fill(question);
  await input.press("Enter");
  await expect(page.getByTestId("composer-input")).toHaveValue("", T);
}

async function waitForAnswer(page) {
  const card = page.getByTestId("answer-card");
  await expect(card).toBeVisible(ANSWER);
  await expect(page.getByTestId("answer-check")).toHaveCount(0, ANSWER);
  const status = await card.getAttribute("data-status");
  const header = await page.getByTestId("timeline-header").last().textContent();
  return `${status}, ${header}`;
}

async function openTimeline(page) {
  const header = page.getByTestId("timeline-header").last();
  if ((await header.getAttribute("aria-expanded")) !== "true") await header.click();
}

/** Waits until the source panel has drawn the PDF page and marked the quote. */
async function pdfReady(page, key) {
  const panel = page.getByTestId("source-panel");
  await expect(panel.getByText("Hämtar dokumentet")).toHaveCount(0, T);
  await panel.locator("mark.quote-mark").first().waitFor(T).catch(() => log(`${key}: no mark`));
}

async function shot(page, name, fullPage = false) {
  await page.waitForTimeout(400); // let the fold and highlight animations finish
  await page.screenshot({ path: `${outDir}/${name}`, fullPage });
  log(`saved ${name}`);
}

async function run(name, body) {
  try {
    await body();
  } catch (error) {
    log(`${name} FAILED: ${String(error).split("\n")[0]}`);
  }
}

const Q = {
  q1: "Vad är uppsägningstiden i IT-driftavtalet?",
  q2: "Vilket avtalsnummer och organisationsnummer har Nordlo Advance på IT-drift Mindre, och har bolaget hetat något annat tidigare?",
  q3: "Vår inhyrda IT-tekniker, avropad genom rangordning, behöver jobba en lördag. Vad får bemanningsföretaget ta betalt för de timmarna?",
  q4: "Hur många anbud skulle enligt utvärderingsmodellen antas i upphandlingen av IT-drift Mindre?",
  q5: "Vilket bemanningsföretag är rangordnat som nummer ett för kontorstjänster i Stockholm?",
  f: "Jämför punkt 7 om skadestånd i mitt avtal med ramavtalet för IT-konsulttjänster. Vad avviker?",
};

await run("start", async () => {
  const page = await fresh();
  await shot(page, "01-start.png");
  await page.context().close();
});

// Demo question 3: the live timeline, the folded answer, the steps opened again, the source.
await run("q3", async () => {
  const page = await fresh();
  await ask(page, Q.q3);
  // Shoot the live timeline once three steps are in, unless the answer comes first.
  const steps = page.getByTestId("agent-step");
  await expect(steps.nth(2).or(page.getByTestId("answer-card"))).toBeVisible(ANSWER);
  if (await steps.nth(2).isVisible()) await shot(page, "02-steg-live.png");
  else log("q3: answered before three steps were shown");
  log(`q3: ${await waitForAnswer(page)}`);
  await shot(page, "03-svar.png");
  await openTimeline(page);
  log(`q3 thoughts: ${await page.getByTestId("thought").count()}`);
  await shot(page, "03b-steg-oppnade.png", true);
  await page.getByTestId("timeline-header").last().click(); // fold again for the source view
  const ref = page.getByTestId("answer-card").getByTestId("ref-1");
  if (!(await ref.count())) throw new Error("the answer has no source to open");
  await ref.first().click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toBeVisible(T);
  await pdfReady(page, "q3");
  log(`q3 panel: ${(await panel.textContent()).slice(0, 160)}`);
  await shot(page, "04-kalla-markerad.png");
  await page.context().close();
});

// Demo question 1: the agent's question in the chat with buttons, then the answer.
await run("q1", async () => {
  const page = await fresh();
  await ask(page, Q.q1);
  const question = page.getByTestId("clarify-card");
  const card = page.getByTestId("answer-card");
  await expect(question.or(card)).toBeVisible(ANSWER);
  if (await question.isVisible()) {
    const buttons = question.getByRole("button");
    const labels = await buttons.allTextContents();
    log(`q1 asked: ${(await question.textContent()).slice(0, 200)} | ${JSON.stringify(labels)}`);
    await shot(page, "05-motfraga.png");
    const pick = question.getByRole("button", { name: /utan särskilt skäl/ });
    await ((await pick.count()) ? pick.first() : buttons.first()).click();
  } else {
    log("q1 answered without asking");
  }
  log(`q1: ${await waitForAnswer(page)}`);
  await shot(page, "05b-motfraga-svar.png");
  await page.context().close();
});

for (const [key, name] of [
  ["q2", "06-register.png"],
  ["q4", "07-rattelse.png"],
  ["q5", "08-inget-svar.png"],
]) {
  await run(key, async () => {
    const page = await fresh();
    await ask(page, Q[key]);
    log(`${key}: ${await waitForAnswer(page)}`);
    await shot(page, name);
    await page.context().close();
  });
}

// Demo step F: the paperclip, the agent reading the file, sources from "Din fil".
await run("f", async () => {
  const page = await fresh();
  await page.getByTestId("file-input").setInputFiles(FILE);
  const chip = page.getByTestId("file-chip");
  await expect(chip).toHaveAttribute("data-status", "ready", T);
  await shot(page, "09-fil-bifogad.png");
  await ask(page, Q.f);
  log(`f: ${await waitForAnswer(page)}`);
  await shot(page, "09b-egen-fil-svar.png");
  await openTimeline(page);
  await shot(page, "09c-egen-fil-steg.png", true);
  await page.getByTestId("timeline-header").last().click();
  const card = page.getByTestId("answer-card");
  const ownSources = card
    .locator('[data-testid^="source-"]')
    .filter({ has: page.getByTestId("own-file") });
  if (await ownSources.count()) {
    const id = (await ownSources.first().getAttribute("data-testid")).replace("source-", "");
    await page.getByTestId("answer-card").getByTestId(`ref-${id}`).first().click();
    const panel = page.getByTestId("source-panel");
    await expect(panel).toBeVisible(T);
    await pdfReady(page, "f");
    log(`f panel: ${(await panel.textContent()).slice(0, 160)}`);
    await shot(page, "09d-egen-fil-kalla.png");
  } else {
    log("f: no source from the file");
  }
  await page.context().close();
});

// The same answer in the dark theme, for the slide on readability.
await run("dark", async () => {
  const page = await fresh("dark");
  await ask(page, Q.q2);
  log(`dark: ${await waitForAnswer(page)}`);
  await shot(page, "10-morkt-tema.png");
  await page.context().close();
});

await browser.close();
