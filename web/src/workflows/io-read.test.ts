import { afterEach, describe, expect, it, vi } from "vitest";
import { readText } from "./IoControls";

/** A File without `text()`, so readText takes the FileReader fallback. */
function legacyFile(): File {
  const file = new File(["{}"], "wf.json", { type: "application/json" });
  Object.defineProperty(file, "text", { value: undefined });
  return file;
}

class FailingReader {
  error: DOMException | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(private readonly failure: DOMException | null) {}
  readAsText() {
    queueMicrotask(() => {
      this.error = this.failure;
      this.onerror?.();
    });
  }
}

describe("readText", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads through FileReader when File.text is missing", async () => {
    await expect(readText(legacyFile())).resolves.toBe("{}");
  });

  it("rejects with an Error naming the reader's failure", async () => {
    vi.stubGlobal(
      "FileReader",
      class extends FailingReader {
        constructor() {
          super(new DOMException("disk went away", "NotReadableError"));
        }
      },
    );
    const err = await readText(legacyFile()).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toContain("disk went away");
  });

  it("rejects with an Error even when the reader reports no error object", async () => {
    vi.stubGlobal(
      "FileReader",
      class extends FailingReader {
        constructor() {
          super(null);
        }
      },
    );
    const err = await readText(legacyFile()).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toContain("wf.json");
  });
});
