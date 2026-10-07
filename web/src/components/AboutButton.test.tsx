import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AboutButton } from "./AboutButton";
import { mockFetch } from "../test/mockApi";

const LINES = [
  "1 quiet — wait 300 s; stop if the PR head moves (head_unchanged, as github-app)",
  "3 fix — retry up to 3×, until verdict ∈ {pass, no_gate}:",
  "  3.1 agent — qwen-fixer (agent)",
];

function setup(status = 200, stale = false) {
  const api = mockFetch({
    "/api/workflows/pr-fixer/describe": {
      status,
      body:
        status === 200
          ? { id: "pr-fixer", kind: "workflow", lines: LINES, entries: [] }
          : { error: { code: "not_found", message: "workflows/pr-fixer does not exist", errors: [] } },
    },
  });
  render(
    <div>
      <AboutButton noun="workflows" id="pr-fixer" name="PR fixer" stale={stale} />
      <button type="button">elsewhere</button>
    </div>,
  );
  return { ...api, button: screen.getByRole("button", { name: "About PR fixer" }) };
}

describe("AboutButton (d19)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("is labelled, collapsed and controls a hidden panel; nothing is fetched until it opens", () => {
    const { button, calls } = setup();
    expect(button).toHaveAttribute("aria-expanded", "false");
    const panelId = button.getAttribute("aria-controls")!;
    expect(document.getElementById(panelId)).not.toBeVisible();
    expect(calls).toEqual([]);
  });

  it("a click opens the panel with the API's lines verbatim (indentation and symbols kept)", async () => {
    const { button, calls } = setup();
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("dialog", { name: "About PR fixer" });
    expect(panel).toHaveAttribute("aria-modal", "false");
    expect(panel.id).toBe(button.getAttribute("aria-controls"));
    const pre = await within(panel).findByTestId("about-lines");
    expect(pre.tagName).toBe("PRE");
    expect(pre.textContent).toBe(LINES.join("\n"));
    expect(calls).toEqual(["/api/workflows/pr-fixer/describe"]);
    expect(panel).toHaveFocus(); // focus moves into the panel
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("Enter opens it, Escape closes it and returns focus to the button; Space opens it too", async () => {
    const user = userEvent.setup();
    const { button } = setup();
    button.focus();
    await user.keyboard("{Enter}");
    const panel = screen.getByRole("dialog", { name: "About PR fixer" });
    await within(panel).findByTestId("about-lines");
    expect(panel).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
    await user.keyboard(" ");
    expect(button).toHaveAttribute("aria-expanded", "true");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("a press outside closes it; Escape while closed is left alone", async () => {
    const { button } = setup();
    await userEvent.click(button);
    await screen.findByTestId("about-lines");
    fireEvent.pointerDown(screen.getByRole("button", { name: "elsewhere" }));
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.focus();
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    act(() => {
      button.dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(false);
  });

  it("Copy puts the lines on the clipboard and says so", async () => {
    const { button } = setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await userEvent.click(button);
    await screen.findByTestId("about-lines");
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(LINES.join("\n"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("names a failed call, and notes the saved version for a dirty draft", async () => {
    setup(404);
    await userEvent.click(screen.getByRole("button", { name: "About PR fixer" }));
    expect(await screen.findByText("workflows/pr-fixer does not exist")).toBeInTheDocument();
  });

  it("a stale description says it is the saved version", async () => {
    const { button } = setup(200, true);
    await userEvent.click(button);
    await waitFor(() => expect(screen.getByText("The saved version.")).toBeInTheDocument());
  });
});
