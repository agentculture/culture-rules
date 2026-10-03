import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { failureMessage, settleAll } from "./settle";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("settleAll", () => {
  it("hands every outcome, fulfilled or rejected, to onSettled", async () => {
    const onSettled = vi.fn();
    const onFailed = vi.fn();
    settleAll([Promise.resolve(1), Promise.reject(new ApiError(500, "boom", "store down"))], onSettled, onFailed);
    await flush();
    const [[results]] = onSettled.mock.calls;
    expect(results[0]).toEqual({ status: "fulfilled", value: 1 });
    expect(results[1].status).toBe("rejected");
    expect(onFailed).not.toHaveBeenCalled();
  });

  it("surfaces a failure inside onSettled through onFailed instead of floating it", async () => {
    const onFailed = vi.fn();
    settleAll(
      [Promise.resolve(1)],
      () => {
        throw new Error("could not apply the load");
      },
      onFailed,
    );
    await flush();
    expect(onFailed).toHaveBeenCalledWith("could not apply the load");
  });

  it("surfaces a rejection reason that cannot even be turned into a string", async () => {
    const onFailed = vi.fn();
    const unprintable = Object.create(null) as object;
    settleAll(
      [Promise.reject(unprintable)],
      (results) => {
        // A view describing the reason with String(...) throws on a null-prototype object.
        String(results[0].status === "rejected" ? results[0].reason : "");
      },
      onFailed,
    );
    await flush();
    expect(onFailed).toHaveBeenCalledTimes(1);
    expect(onFailed.mock.calls[0][0]).toMatch(/primitive/i);
  });
});

describe("failureMessage", () => {
  it("is the message of an Error, the text of anything else, and never throws", () => {
    expect(failureMessage(new ApiError(404, "not_found", "no such rule"))).toBe("no such rule");
    expect(failureMessage("plain")).toBe("plain");
    expect(failureMessage(Object.create(null))).toBe("unexpected error");
  });
});
