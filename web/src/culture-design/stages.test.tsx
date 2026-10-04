import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Switch } from "./stages";

describe("Switch disabled state", () => {
  it("marks aria-disabled and ignores clicks while disabled, staying focusable", async () => {
    const onChange = vi.fn();
    render(<Switch label="Rule enabled" checked disabled onChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "Rule enabled" });
    expect(sw).toHaveAttribute("aria-disabled", "true");
    expect(sw).not.toBeDisabled(); // aria-disabled, so the 44px hit area and focus stay
    await userEvent.setup().click(sw);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("is not aria-disabled by default and still toggles", async () => {
    const onChange = vi.fn();
    render(<Switch label="Rule enabled" checked onChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "Rule enabled" });
    expect(sw).not.toHaveAttribute("aria-disabled");
    await userEvent.setup().click(sw);
    expect(onChange).toHaveBeenCalledWith(false);
  });
});
