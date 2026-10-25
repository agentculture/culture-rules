import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import type { Action } from "../api/types";
import { ACTORS } from "../fixtures/rules-fixture";
import ActionPicker, { actionProblem, actorsFor, blankAction } from "./ActionPicker";

/* The daily-newspaper kinds (#53): email.send, search.web, llm.complete, library.push. */

const NEWSPAPER_ACTORS: Actor[] = [
  ...ACTORS,
  {
    id: "mailer",
    name: "Mailer",
    kind: "app",
    enabled: true,
    params: { surface: "email", connection: { api_key: "grant:resend", recipients: [] }, actions: [] },
  },
  {
    id: "mailer-off",
    name: "Old mailer",
    kind: "app",
    enabled: false,
    params: { surface: "email", connection: { api_key: "grant:resend" } },
  },
  { id: "brave", name: "Brave", kind: "app", enabled: true, params: { surface: "search", connection: {} } },
  { id: "cortex", name: "Cortex", kind: "agent", enabled: true, params: { base_url: "http://localhost:8000", model: "qwen" } },
  { id: "plain-agent", name: "Plain agent", kind: "agent", enabled: true },
  {
    id: "xteink",
    name: "xteink",
    kind: "service",
    enabled: true,
    params: {
      base_url: "http://localhost:8080",
      upload_path: "/api/library",
      queue_path: "/api/devices/{device_id}/queue",
      kinds: ["newspaper", "transcript"],
      visibilities: ["private", "public"],
      formats: ["epub", "md"],
    },
  },
  {
    id: "library",
    name: "Culture library",
    kind: "service",
    enabled: true,
    params: { base_url: "http://localhost:8796", upload_path: "/api/library", kinds: ["newspaper"] },
  },
];

function Harness({ start }: Readonly<{ start?: Action }>) {
  const [value, setValue] = useState<Action>(start ?? blankAction());
  return (
    <>
      <ActionPicker value={value} actors={NEWSPAPER_ACTORS} triggerType="schedule" onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Action;
const optionValues = (label: string) =>
  within(screen.getByLabelText(label))
    .getAllByRole("option")
    .map((o) => o.getAttribute("value"));
const ids = (kind: string) => actorsFor(kind, NEWSPAPER_ACTORS).map((a) => a.id);

async function pick(kind: string, actor: string) {
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("What happens"), kind);
  await user.selectOptions(screen.getByLabelText("Actor"), actor);
  return user;
}

describe("newspaper kinds offered", () => {
  it("offers all four once a suitable enabled actor exists", () => {
    render(<Harness />);
    expect(optionValues("What happens")).toEqual(
      expect.arrayContaining(["email.send", "search.web", "llm.complete", "library.push"]),
    );
  });

  it("limits each kind to the actors that can perform it", () => {
    expect(ids("email.send")).toEqual(["mailer"]);
    expect(ids("search.web")).toEqual(["brave"]);
    expect(ids("llm.complete")).toEqual(["cortex"]);
    expect(ids("library.push")).toEqual(["xteink", "library"]);
  });
});

describe("email.send", () => {
  it("saves recipients as a list with a subject and an HTML body", async () => {
    render(<Harness />);
    const user = await pick("email.send", "mailer");
    await user.type(screen.getByLabelText("To"), "a@culture.dev, b@culture.dev");
    await user.type(screen.getByLabelText("Subject"), "Daily newspaper");
    await user.selectOptions(screen.getByLabelText("Map Html"), "__custom__");
    await user.type(screen.getByLabelText("Path for Html"), "workflow.outputs.html");
    expect(saved()).toEqual({
      kind: "email.send",
      params: {
        actor: "mailer",
        to: ["a@culture.dev", "b@culture.dev"],
        subject: "Daily newspaper",
        html: "workflow.outputs.html",
      },
    });
    expect(actionProblem(saved())).toBeNull();
  });

  it("needs recipients and a subject, and refuses a non-list recipient value", () => {
    expect(actionProblem({ kind: "email.send", params: { actor: "mailer", subject: "x" } })).toBe("empty");
    expect(actionProblem({ kind: "email.send", params: { actor: "mailer", to: [], subject: "x" } })).toBe("empty");
    expect(actionProblem({ kind: "email.send", params: { actor: "mailer", to: 3, subject: "x" } })).toBe(
      "invalid_value",
    );
    expect(actionProblem({ kind: "email.send", params: { to: ["a@b.c"], subject: "x" } })).toBe("no_actor_port");
  });

  it("shows stored recipients as text", () => {
    render(<Harness start={{ kind: "email.send", params: { actor: "mailer", to: ["a@x.org", "b@y.org"], subject: "s" } }} />);
    expect(screen.getByLabelText("To")).toHaveValue("a@x.org, b@y.org");
  });
});

describe("search.web", () => {
  it("saves the query, an integer count and a freshness picked from a select", async () => {
    render(<Harness />);
    const user = await pick("search.web", "brave");
    await user.type(screen.getByLabelText("Query"), "jetson news");
    await user.type(screen.getByLabelText("Count"), "5");
    expect(optionValues("Freshness")).toEqual(["", "pd", "pw", "pm", "py"]);
    await user.selectOptions(screen.getByLabelText("Freshness"), "pd");
    await user.type(screen.getByLabelText("Country"), "US");
    expect(saved()).toEqual({
      kind: "search.web",
      params: { actor: "brave", query: "jetson news", count: 5, freshness: "pd", country: "US" },
    });
    expect(actionProblem(saved())).toBeNull();
  });

  it("needs a query and refuses a freshness outside the four windows", () => {
    expect(actionProblem({ kind: "search.web", params: { actor: "brave" } })).toBe("empty");
    expect(actionProblem({ kind: "search.web", params: { actor: "brave", query: "q", freshness: "pz" } })).toBe(
      "invalid_value",
    );
  });
});

describe("llm.complete", () => {
  it("saves mapped content, max tokens and a numeric temperature", async () => {
    render(<Harness />);
    const user = await pick("llm.complete", "cortex");
    await user.selectOptions(screen.getByLabelText("Map Content"), "__custom__");
    await user.type(screen.getByLabelText("Path for Content"), "workflow.outputs.results");
    await user.type(screen.getByLabelText("Max tokens"), "800");
    await user.type(screen.getByLabelText("Temperature"), "0.2");
    expect(saved()).toEqual({
      kind: "llm.complete",
      params: { actor: "cortex", content: "workflow.outputs.results", max_tokens: 800, temperature: 0.2 },
    });
    expect(actionProblem(saved())).toBeNull();
  });

  it("needs content", () => {
    expect(actionProblem({ kind: "llm.complete", params: { actor: "cortex" } })).toBe("empty");
  });
});

describe("library.push", () => {
  it("saves the EPUB reference, title, kind, visibility and device", async () => {
    render(<Harness />);
    const user = await pick("library.push", "xteink");
    await user.selectOptions(screen.getByLabelText("Map Content base64"), "__custom__");
    await user.type(screen.getByLabelText("Path for Content base64"), "workflow.outputs.epub");
    await user.type(screen.getByLabelText("Title"), "Daily newspaper");
    await user.selectOptions(screen.getByLabelText("Kind"), "newspaper");
    await user.selectOptions(screen.getByLabelText("Visibility"), "public");
    await user.type(screen.getByLabelText("Device id"), "reader-1");
    expect(saved()).toEqual({
      kind: "library.push",
      params: {
        actor: "xteink",
        content_base64: "workflow.outputs.epub",
        title: "Daily newspaper",
        kind: "newspaper",
        visibility: "public",
        device_id: "reader-1",
      },
    });
    expect(actionProblem(saved())).toBeNull();
  });

  it("offers the kinds and visibilities the library actor lists", async () => {
    render(<Harness />);
    await pick("library.push", "xteink");
    const values = (label: string) =>
      within(screen.getByLabelText(label))
        .getAllByRole("option")
        .map((o) => (o as HTMLOptionElement).value);
    expect(values("Kind")).toEqual(["", "newspaper", "transcript"]);
    expect(values("Visibility")).toEqual(["", "private", "public"]);
  });

  it("hides the device id without a queue_path and the visibility without visibilities", async () => {
    render(<Harness />);
    await pick("library.push", "library");
    expect(screen.queryByLabelText("Device id")).toBeNull();
    expect(screen.queryByLabelText("Visibility")).toBeNull();
    expect(optionValues("Kind")).toEqual(["", "newspaper"]);
  });

  it("drops a device id, kind or visibility the newly chosen actor does not use or list", async () => {
    render(<Harness />);
    const user = await pick("library.push", "xteink");
    await user.selectOptions(screen.getByLabelText("Kind"), "transcript");
    await user.selectOptions(screen.getByLabelText("Visibility"), "public");
    await user.type(screen.getByLabelText("Device id"), "reader-1");
    await user.selectOptions(screen.getByLabelText("Actor"), "library");
    expect(saved().params).toEqual({ actor: "library" });
  });

  it("offers the formats the library actor lists and drops one the next actor does not list", async () => {
    render(<Harness />);
    const user = await pick("library.push", "xteink");
    expect(optionValues("Format")).toEqual(["", "epub", "md"]);
    await user.selectOptions(screen.getByLabelText("Format"), "md");
    expect(saved().params?.format).toBe("md");
    await user.selectOptions(screen.getByLabelText("Actor"), "library");
    expect(saved().params?.format).toBeUndefined();
  });

  it("keeps a kind the newly chosen actor also lists", async () => {
    render(<Harness />);
    const user = await pick("library.push", "xteink");
    await user.selectOptions(screen.getByLabelText("Kind"), "newspaper");
    await user.selectOptions(screen.getByLabelText("Actor"), "library");
    expect(saved().params).toEqual({ actor: "library", kind: "newspaper" });
  });

  it("needs the content, a title and a kind", () => {
    const full = { actor: "xteink", content_base64: "x", title: "t", kind: "newspaper" };
    expect(actionProblem({ kind: "library.push", params: { ...full, content_base64: undefined } })).toBe("empty");
    expect(actionProblem({ kind: "library.push", params: { ...full, title: undefined } })).toBe("empty");
    expect(actionProblem({ kind: "library.push", params: { ...full, kind: undefined } })).toBe("empty");
    expect(actionProblem({ kind: "library.push", params: full })).toBeNull();
  });
});
