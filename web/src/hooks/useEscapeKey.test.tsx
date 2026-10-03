import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { useEscapeKey } from "./useEscapeKey";

function Harness({ onEscape }: { onEscape: () => void }) {
  const ref = useRef<HTMLFormElement>(null);
  useEscapeKey(ref, onEscape);
  return (
    <div>
      <button type="button">outside</button>
      <form ref={ref} aria-label="inside">
        <input aria-label="field" />
        <ul
          role="listbox"
          aria-label="menu"
          tabIndex={0}
          onKeyDown={(e) => {
            // An inner widget that handles Escape itself claims it with preventDefault.
            if (e.key === "Escape") e.preventDefault();
          }}
        />
      </form>
    </div>
  );
}

/** A container with its own Escape handling around the harness (e.g. a panel around a form). */
function Nested({ onEscape, onOuter }: { onEscape: () => void; onOuter: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEscapeKey(ref, onOuter);
  return (
    <div ref={ref}>
      <Harness onEscape={onEscape} />
    </div>
  );
}

describe("useEscapeKey", () => {
  it("calls back when Escape is pressed with focus inside the element", async () => {
    const onEscape = vi.fn();
    const user = userEvent.setup();
    render(<Harness onEscape={onEscape} />);
    await user.click(screen.getByLabelText("field"));
    await user.keyboard("{Escape}");
    expect(onEscape).toHaveBeenCalledTimes(1);
  });

  it("ignores Escape outside the element and other keys inside it", async () => {
    const onEscape = vi.fn();
    const user = userEvent.setup();
    render(<Harness onEscape={onEscape} />);
    await user.click(screen.getByRole("button", { name: "outside" }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByLabelText("field"));
    await user.keyboard("a{Enter}");
    expect(onEscape).not.toHaveBeenCalled();
  });

  it("lets an inner widget that handles Escape itself win", async () => {
    const onEscape = vi.fn();
    const user = userEvent.setup();
    render(<Harness onEscape={onEscape} />);
    screen.getByRole("listbox", { name: "menu" }).focus();
    await user.keyboard("{Escape}");
    expect(onEscape).not.toHaveBeenCalled();
  });

  it("stops the Escape it handled from reaching listeners further out", async () => {
    const onEscape = vi.fn();
    const outer = vi.fn();
    const onWindow = vi.fn();
    window.addEventListener("keydown", onWindow);
    const user = userEvent.setup();
    render(<Nested onEscape={onEscape} onOuter={outer} />);
    await user.click(screen.getByLabelText("field"));
    await user.keyboard("{Escape}");
    window.removeEventListener("keydown", onWindow);
    expect(onEscape).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
    expect(onWindow).not.toHaveBeenCalled();
  });

  it("uses the latest callback without re-subscribing", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<Harness onEscape={first} />);
    rerender(<Harness onEscape={second} />);
    await user.click(screen.getByLabelText("field"));
    await user.keyboard("{Escape}");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
