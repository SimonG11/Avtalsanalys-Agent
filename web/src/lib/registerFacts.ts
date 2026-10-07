/**
 * What: the register rows of an answer (`register_facts`), grouped into one line per agreement.
 *
 * Why: the register has a row per agreement and sub-area (and region), so one agreement can
 * come many times: a question about Bemanningstjänster gave 56 rows. The reader wants the
 * agreements, not the rows. The register also writes some agreement numbers two ways ("-001"
 * and "-01"); they are one agreement.
 *
 * How: `agreementKey` writes the supplier's sequence with three digits, as the backend's
 * `domain/identifiers.py` does, and `groupRegisterFacts` keeps the rows of each key together
 * in the order they came. `describeAgreement` makes the line the answer card shows, with the
 * same parts as the command line's `register_lines` (agent/__main__.py).
 */
import type { RegisterFact } from "./contract.ts";

/** The sequence after the case number: "-001", "-01" or ":018", maybe with a variant "-A". */
const SEQUENCE = /^(.+?)[-–:](\d{2,3})(-[A-Z])?$/;

/** "23.3-12000-2020-01" and "23.3-12000-2020-001" give the same key. */
export function agreementKey(agreementNumber: string): string {
  const number = agreementNumber.trim();
  const match = SEQUENCE.exec(number);
  if (match === null) return number;
  const [, procurement, sequence, variant] = match;
  return `${procurement}-${sequence.padStart(3, "0")}${variant ?? ""}`;
}

/** The rows grouped per agreement, in the order the agreements first appear. */
export function groupRegisterFacts(facts: readonly RegisterFact[]): RegisterFact[][] {
  const agreements = new Map<string, RegisterFact[]>();
  for (const fact of facts) {
    const key = agreementKey(fact.agreement_number);
    agreements.set(key, [...(agreements.get(key) ?? []), fact]);
  }
  return [...agreements.values()];
}

export interface AgreementDescription {
  /** The number as the register's first row writes it. */
  agreementNumber: string;
  /** "Nordlo Advance AB (556486-1689)". */
  supplier: string;
  formerNames: string[];
  /** The sub-area, or "56 delområden" when the agreement has several. */
  where: string;
  /** "2024-11-14 – 2028-11-13, längst till 2030-11-13", or that the rows differ. */
  period: string;
}

export function describeAgreement(rows: readonly RegisterFact[]): AgreementDescription {
  const first = rows[0];
  const periods = new Set(
    rows.map((row) => `${row.valid_from}|${row.valid_to}|${row.max_extension_to ?? ""}`),
  );
  let period = "olika giltighetstider";
  if (periods.size === 1) {
    period = `${first.valid_from} – ${first.valid_to}`;
    if (first.max_extension_to !== null) period += `, längst till ${first.max_extension_to}`;
  }
  return {
    agreementNumber: first.agreement_number,
    supplier: first.org_number
      ? `${first.supplier_name} (${first.org_number})`
      : first.supplier_name,
    formerNames: first.former_names,
    where: rows.length === 1 ? first.sub_area : `${rows.length} delområden`,
    period,
  };
}
