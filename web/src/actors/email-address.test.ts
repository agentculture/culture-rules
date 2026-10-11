import { describe, expect, it } from "vitest";
import { isEmailAddress } from "./app-config";

// The loose client-side check (app_actor.py `is_email_address` is the authority), written
// without a regex so its run time stays linear on any input (Sonar S8786, PR #54).
describe("isEmailAddress", () => {
  it.each(["a@culture.dev", "first.last@mail.example.org", "a@b.c.", "x+tag@a.b"])("accepts %s", (text) => {
    expect(isEmailAddress(text)).toBe(true);
  });

  it.each(["", "nobody", "@culture.dev", "a@", "a@b", "a@.b", "a@b.", "a@@b.c", "a@b@c.d", "a b@c.d", "a@b.c\n"])(
    "refuses %j",
    (text) => {
      expect(isEmailAddress(text)).toBe(false);
    },
  );

  it("answers a long input without backtracking", () => {
    expect(isEmailAddress("a@" + "b".repeat(50_000))).toBe(false);
  });
});
