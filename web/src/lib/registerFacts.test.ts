import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { RegisterFact } from "./contract.ts";
import { agreementKey, describeAgreement, groupRegisterFacts } from "./registerFacts.ts";

function fact(fields: Partial<RegisterFact>): RegisterFact {
  return {
    agreement_number: "00.0-0000-2026-001",
    supplier_name: "Exempelleverantören AB",
    former_names: [],
    org_number: "000000-0000",
    sub_area: "IT-drift / IT-drift Större",
    valid_from: "2026-01-01",
    valid_to: "2028-12-31",
    max_extension_to: null,
    ...fields,
  };
}

describe("agreementKey", () => {
  it("writes the sequence with three digits, so both spellings are one agreement", () => {
    assert.equal(agreementKey("23.3-12000-2020-01"), "23.3-12000-2020-001");
    assert.equal(agreementKey("23.3-12000-2020-001"), "23.3-12000-2020-001");
    assert.equal(agreementKey("23.3-2940-2020:018"), "23.3-2940-2020-018");
    assert.equal(agreementKey("23.3-2649-2022-003-A"), "23.3-2649-2022-003-A");
  });

  it("leaves a number without a sequence as it is", () => {
    assert.equal(agreementKey("6765/05"), "6765/05");
  });
});

describe("groupRegisterFacts", () => {
  it("keeps the rows of one agreement together, in the order the agreements come", () => {
    const groups = groupRegisterFacts([
      fact({ sub_area: "A" }),
      fact({ agreement_number: "00.0-0000-2026-002", sub_area: "B" }),
      fact({ agreement_number: "00.0-0000-2026-01", sub_area: "C" }),
    ]);
    assert.deepEqual(
      groups.map((rows) => rows.map((row) => row.sub_area)),
      [["A", "C"], ["B"]],
    );
  });
});

describe("describeAgreement", () => {
  it("describes one row with its sub-area and period", () => {
    assert.deepEqual(
      describeAgreement([fact({ former_names: ["EPM Data"], max_extension_to: "2030-12-31" })]),
      {
        agreementNumber: "00.0-0000-2026-001",
        supplier: "Exempelleverantören AB (000000-0000)",
        formerNames: ["EPM Data"],
        where: "IT-drift / IT-drift Större",
        period: "2026-01-01 – 2028-12-31, längst till 2030-12-31",
      },
    );
  });

  it("counts the sub-areas and says when their periods differ", () => {
    const one = describeAgreement([fact({}), fact({ sub_area: "Norr" })]);
    assert.equal(one.where, "2 delområden");
    assert.equal(one.period, "2026-01-01 – 2028-12-31");
    const two = describeAgreement([fact({}), fact({ valid_to: "2027-12-31" })]);
    assert.equal(two.period, "olika giltighetstider");
  });
});
