import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { citationPdfUrl, checkFile, describeUpload, uploadErrorMessage } from "./uploads.ts";

describe("checkFile", () => {
  it("takes PDF, Word and text files up to 10 MB", () => {
    for (const name of ["Avtal.PDF", "bilaga.docx", "anteckningar.txt", "noter.md"]) {
      assert.equal(checkFile({ name, size: 1000 }), null, name);
    }
  });

  it("says why a file cannot be uploaded", () => {
    assert.match(checkFile({ name: "prislista.xlsx", size: 1000 }) ?? "", /Filtypen stöds inte/);
    assert.match(checkFile({ name: "gammal.doc", size: 1000 }) ?? "", /Filtypen stöds inte/);
    assert.equal(
      checkFile({ name: "stor.pdf", size: 10 * 1024 * 1024 + 1 }),
      "Filen är större än 10 MB.",
    );
    assert.equal(checkFile({ name: "tom.txt", size: 0 }), "Filen är tom.");
  });
});

describe("uploadErrorMessage", () => {
  it("prefers the backend's own Swedish detail", () => {
    assert.equal(
      uploadErrorMessage(409, { detail: "Tråden har redan fem filer." }),
      "Tråden har redan fem filer.",
    );
  });

  it("falls back on a message for the status", () => {
    assert.match(uploadErrorMessage(422, {}), /ingen text/);
    assert.match(uploadErrorMessage(413, null), /10 MB/);
    assert.equal(uploadErrorMessage(500, { detail: "" }), "Filen kunde inte laddas upp.");
  });
});

describe("describeUpload", () => {
  const base = { upload_id: "u", filename: "f", warnings: [] };
  it("gives pages for a PDF and sections otherwise", () => {
    assert.equal(describeUpload({ ...base, kind: "pdf", pages: 12, sections: 30 }), "12 sidor");
    assert.equal(describeUpload({ ...base, kind: "pdf", pages: 1 }), "1 sida");
    assert.equal(describeUpload({ ...base, kind: "docx", pages: null, sections: 3 }), "3 avsnitt");
    assert.equal(describeUpload({ ...base, kind: "text" }), "Textfil");
  });
});

describe("citationPdfUrl", () => {
  const sha256 = "a".repeat(64);
  it("opens an agreement's PDF by its hash", () => {
    const citation = { source: "framework" as const, sha256, upload_id: null, page: 3 };
    assert.equal(citationPdfUrl(citation, "t1"), `/api/documents/${sha256}/pdf`);
  });

  it("opens an uploaded PDF in its thread, even when the citation has a hash", () => {
    const citation = { source: "upload" as const, sha256, upload_id: "upl 1", page: 2 };
    assert.equal(citationPdfUrl(citation, "t/1"), "/api/uploads/upl%201/file?thread_id=t%2F1");
  });

  it("has nothing to show for an uploaded Word or text file", () => {
    const citation = { source: "upload" as const, sha256: null, upload_id: "upl_1", page: null };
    assert.equal(citationPdfUrl(citation, "t1"), null);
  });
});
