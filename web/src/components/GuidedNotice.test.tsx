import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import GuidedNotice from "./GuidedNotice";

describe("GuidedNotice", () => {
  it("shows the plain message and a button per fix, handing the fix to onFix", async () => {
    const onFix = vi.fn();
    render(<GuidedNotice error={new ApiError(422, "actor_unavailable", "raw: no host")} onFix={onFix} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/raw: no host/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /pick an actor/i }));
    expect(onFix).toHaveBeenCalledWith({ kind: "pick-actor" });
  });

  it("renders the generic guidance, not raw text, for an unknown code", () => {
    render(<GuidedNotice error={new ApiError(500, "mystery", "Traceback boom")} onFix={() => {}} />);
    expect(screen.queryByText(/Traceback/)).toBeNull();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("omits fixes the host cannot handle when onFix is absent", () => {
    render(<GuidedNotice code="paused" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(/paused/i);
  });
});
