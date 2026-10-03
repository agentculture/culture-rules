import { describe, expect, it, vi } from "vitest";
import { readText } from "./IoControls";

describe("readText", () => {
  it("reads a chosen file's text through Blob#text", async () => {
    const file = new File(['{"id":"wf"}'], "wf.json", { type: "application/json" });
    const text = vi.spyOn(file, "text");
    await expect(readText(file)).resolves.toBe('{"id":"wf"}');
    expect(text).toHaveBeenCalledOnce();
  });

  it("passes a read failure through as the rejection", async () => {
    const file = new File(["{}"], "wf.json", { type: "application/json" });
    const failure = new DOMException("disk went away", "NotReadableError");
    vi.spyOn(file, "text").mockRejectedValue(failure);
    await expect(readText(file)).rejects.toBe(failure);
  });
});
