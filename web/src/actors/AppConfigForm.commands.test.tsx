import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { DiscordCommand } from "../api/actors";
import { AppConfigForm } from "./AppConfigForm";
import {
  appDraftFrom,
  appParamsFrom,
  emptyAppDraft,
  validateApp,
  type AppDraft,
  type DiscordCommandDraft,
  type DiscordOptionDraft,
  type FormErrors,
} from "./app-config";

/* d12: a Discord app actor's application commands are its configuration (params.commands). */

const STORED: DiscordCommand[] = [
  {
    name: "save",
    type: "slash",
    description: "Save a message to the library",
    options: [{ name: "message", description: "A message link or id", kind: "message", required: false }],
  },
  { name: "Save to library", type: "message" },
];

const discord = (commands: DiscordCommandDraft[]): AppDraft => ({
  ...emptyAppDraft(),
  surface: "discord",
  connection: { bot_token: "grant:bot" },
  commands,
});
const slash = (patch: Partial<DiscordCommandDraft> = {}): DiscordCommandDraft => ({
  type: "slash",
  name: "save",
  description: "Save it",
  notHere: "",
  options: [],
  ...patch,
});
const message = (patch: Partial<DiscordCommandDraft> = {}): DiscordCommandDraft => ({
  type: "message",
  name: "Save to library",
  description: "",
  notHere: "",
  options: [],
  ...patch,
});
const option = (patch: Partial<DiscordOptionDraft> = {}): DiscordOptionDraft => ({
  name: "text",
  description: "Some text",
  kind: "string",
  required: false,
  ...patch,
});
const errorsOf = (commands: DiscordCommandDraft[]) => validateApp(discord(commands));

describe("Discord commands: draft round-trip", () => {
  it("reads params.commands into rows and writes them back unchanged", () => {
    const params = { surface: "discord" as const, connection: { bot_token: "grant:bot" }, events: [], actions: [], commands: STORED };
    const draft = appDraftFrom(params);
    expect(draft.commands).toEqual([
      {
        type: "slash",
        name: "save",
        description: "Save a message to the library",
        notHere: "",
        options: [{ name: "message", description: "A message link or id", kind: "message", required: false }],
      },
      { type: "message", name: "Save to library", description: "", notHere: "", options: [] },
    ]);
    expect(appParamsFrom(draft, params).commands).toEqual(STORED);
  });

  it("reads a missing commands key as no rows", () => {
    expect(appDraftFrom({ surface: "discord", connection: {}, events: [], actions: [] }).commands).toEqual([]);
  });

  it("omits the commands key when the list is empty", () => {
    const base = { surface: "discord" as const, connection: {}, events: [], actions: [], commands: STORED };
    expect(appParamsFrom(discord([]), base)).not.toHaveProperty("commands");
  });

  it("trims, drops a blank not_here and empty options, and keeps a set not_here", () => {
    const out = appParamsFrom(
      discord([slash({ name: " save ", description: " Save it ", notHere: "  " }), slash({ name: "find", notHere: " Not here. " })]),
      undefined,
    );
    expect(out.commands).toEqual([
      { name: "save", type: "slash", description: "Save it" },
      { name: "find", type: "slash", description: "Save it", not_here: "Not here." },
    ]);
  });

  it("never saves a description or options on a message command", () => {
    const out = appParamsFrom(discord([message({ description: "leftover", options: [option()] })]), undefined);
    expect(out.commands).toEqual([{ name: "Save to library", type: "message" }]);
  });

  it("always saves an option's kind and required boolean", () => {
    const out = appParamsFrom(discord([slash({ options: [option({ name: " text ", required: true })] })]), undefined);
    expect(out.commands).toEqual([
      {
        name: "save",
        type: "slash",
        description: "Save it",
        options: [{ name: "text", description: "Some text", kind: "string", required: true }],
      },
    ]);
  });

  it("does not write commands for a non-Discord surface", () => {
    const draft: AppDraft = { ...emptyAppDraft(), surface: "github", commands: [slash()] };
    expect(appParamsFrom(draft, undefined)).not.toHaveProperty("commands");
  });
});

describe("Discord commands: validation", () => {
  it("accepts the stored example", () => {
    expect(validateApp(appDraftFrom({ surface: "discord", connection: { bot_token: "grant:bot" }, events: [], actions: [], commands: STORED }))).toEqual({});
  });

  it("allows at most 10 commands", () => {
    const ten = Array.from({ length: 10 }, (_, i) => slash({ name: `c${i}` }));
    expect(errorsOf(ten)).toEqual({});
    expect(errorsOf([...ten, slash({ name: "c10" })])).toHaveProperty("commands");
  });

  it("allows at most 5 message commands", () => {
    const five = Array.from({ length: 5 }, (_, i) => message({ name: `Save ${i}` }));
    expect(errorsOf(five)).toEqual({});
    expect(errorsOf([...five, message({ name: "Save 5" })])).toHaveProperty("commands");
  });

  it("checks a slash name against ^[a-z0-9_-]{1,32}$", () => {
    expect(errorsOf([slash({ name: "save-it_2" })])).toEqual({});
    expect(errorsOf([slash({ name: "" })])).toHaveProperty("commands.0.name");
    expect(errorsOf([slash({ name: "Save" })])).toHaveProperty("commands.0.name");
    expect(errorsOf([slash({ name: "save it" })])).toHaveProperty("commands.0.name");
    expect(errorsOf([slash({ name: "a".repeat(33) })])).toHaveProperty("commands.0.name");
    expect(errorsOf([slash({ name: "a".repeat(32) })])).toEqual({});
  });

  it("allows a message name of 1 to 32 characters, not blank", () => {
    expect(errorsOf([message({ name: "Save to library" })])).toEqual({});
    expect(errorsOf([message({ name: "   " })])).toHaveProperty("commands.0.name");
    expect(errorsOf([message({ name: "x".repeat(33) })])).toHaveProperty("commands.0.name");
    expect(errorsOf([message({ name: ` ${"x".repeat(32)} ` })])).toEqual({});
  });

  it("refuses a comma in a message name, which the trigger's Command list would split", () => {
    expect(errorsOf([message({ name: "Save, then share" })])).toHaveProperty("commands.0.name");
  });

  it("refuses a duplicate name within a type and allows it across types", () => {
    expect(errorsOf([slash(), slash()])).toHaveProperty("commands.1.name");
    expect(errorsOf([slash(), slash()])).not.toHaveProperty("commands.0.name");
    expect(errorsOf([message({ name: "save" }), slash({ name: "save" })])).toEqual({});
  });

  it("requires a slash description of 1 to 100 characters", () => {
    expect(errorsOf([slash({ description: " " })])).toHaveProperty("commands.0.description");
    expect(errorsOf([slash({ description: "d".repeat(101) })])).toHaveProperty("commands.0.description");
    expect(errorsOf([slash({ description: "d".repeat(100) })])).toEqual({});
  });

  it("asks no description of a message command", () => {
    expect(errorsOf([message({ description: "" })])).toEqual({});
  });

  it("allows a not_here answer of at most 200 characters", () => {
    expect(errorsOf([slash({ notHere: "n".repeat(200) })])).toEqual({});
    expect(errorsOf([slash({ notHere: "n".repeat(201) })])).toHaveProperty("commands.0.not_here");
  });

  it("allows at most 5 options", () => {
    const five = Array.from({ length: 5 }, (_, i) => option({ name: `o${i}` }));
    expect(errorsOf([slash({ options: five })])).toEqual({});
    expect(errorsOf([slash({ options: [...five, option({ name: "o5" })] })])).toHaveProperty("commands.0.options");
  });

  it("checks an option name against ^[a-z_][a-z0-9_]{0,31}$ and refuses interaction", () => {
    expect(errorsOf([slash({ options: [option({ name: "_a1" })] })])).toEqual({});
    for (const bad of ["", "1a", "a-b", "Text", "a".repeat(33), "interaction", "from", "class", "async"]) {
      expect(errorsOf([slash({ options: [option({ name: bad })] })])).toHaveProperty("commands.0.options.0.name");
    }
  });

  it("refuses a duplicate option name within a command", () => {
    expect(errorsOf([slash({ options: [option(), option()] })])).toHaveProperty("commands.0.options.1.name");
  });

  it("requires an option description of 1 to 100 characters", () => {
    expect(errorsOf([slash({ options: [option({ description: "" })] })])).toHaveProperty("commands.0.options.0.description");
    expect(errorsOf([slash({ options: [option({ description: "d".repeat(101) })] })])).toHaveProperty(
      "commands.0.options.0.description",
    );
  });

  it("allows at most one message-kind option per command", () => {
    const one = [option({ name: "m1", kind: "message" }), option({ name: "t" })];
    expect(errorsOf([slash({ options: one })])).toEqual({});
    const two = [option({ name: "m1", kind: "message" }), option({ name: "m2", kind: "message" })];
    expect(errorsOf([slash({ options: two })])).toHaveProperty("commands.0.options.1.kind");
  });

  it("puts required options before optional ones", () => {
    const ok = [option({ name: "a", required: true }), option({ name: "b" })];
    expect(errorsOf([slash({ options: ok })])).toEqual({});
    const bad = [option({ name: "a" }), option({ name: "b", required: true })];
    expect(errorsOf([slash({ options: bad })])).toHaveProperty("commands.0.options.1.required");
  });

  it("ignores options left on a message command (they are never saved)", () => {
    expect(errorsOf([message({ options: [option({ name: "BAD" })] })])).toEqual({});
  });

  it("does not check commands on another surface", () => {
    const draft: AppDraft = { ...emptyAppDraft(), surface: "search", connection: { api_key: "grant:k" }, commands: [slash({ name: "BAD" })] };
    expect(validateApp(draft)).toEqual({});
  });
});

let latest: AppDraft = emptyAppDraft();
function Harness({ initial, errors }: Readonly<{ initial: AppDraft; errors?: FormErrors }>) {
  const [draft, setDraft] = useState(initial);
  latest = draft;
  return <AppConfigForm value={draft} onChange={setDraft} errors={errors} />;
}

describe("Discord commands: the form", () => {
  it("shows a Commands section on Discord only", () => {
    const { unmount } = render(<Harness initial={{ ...emptyAppDraft(), surface: "github" }} />);
    expect(screen.queryByRole("group", { name: "Commands" })).toBeNull();
    unmount();
    render(<Harness initial={discord([])} />);
    expect(screen.getByRole("group", { name: "Commands" })).toBeInTheDocument();
  });

  it("adds a command row and an option row, and edits them", async () => {
    const user = userEvent.setup();
    render(<Harness initial={discord([])} />);
    await user.click(screen.getByRole("button", { name: "Add command" }));
    const row = screen.getByRole("group", { name: "Command 1" });
    expect(within(row).getByRole("combobox", { name: "Command 1 type" })).toHaveValue("slash");
    await user.type(within(row).getByRole("textbox", { name: "Command 1 name" }), "save");
    await user.type(within(row).getByRole("textbox", { name: "Command 1 description" }), "Save a message");
    await user.type(within(row).getByRole("textbox", { name: "Command 1 answer when not set up here" }), "Not here.");
    await user.click(within(row).getByRole("button", { name: "Add option to command 1" }));
    await user.type(within(row).getByRole("textbox", { name: "Command 1 option 1 name" }), "message");
    await user.type(within(row).getByRole("textbox", { name: "Command 1 option 1 description" }), "A link");
    await user.selectOptions(within(row).getByRole("combobox", { name: "Command 1 option 1 kind" }), "message");
    await user.click(within(row).getByRole("checkbox", { name: "Command 1 option 1 required" }));
    expect(latest.commands).toEqual([
      {
        type: "slash",
        name: "save",
        description: "Save a message",
        notHere: "Not here.",
        options: [{ name: "message", description: "A link", kind: "message", required: true }],
      },
    ]);
    await user.click(within(row).getByRole("button", { name: "Remove command 1 option 1" }));
    expect(latest.commands[0].options).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Remove command 1" }));
    expect(latest.commands).toEqual([]);
  });

  it("hides description and options for a message command", async () => {
    const user = userEvent.setup();
    render(<Harness initial={discord([slash()])} />);
    const row = screen.getByRole("group", { name: "Command 1" });
    expect(within(row).getByRole("textbox", { name: "Command 1 description" })).toBeInTheDocument();
    await user.selectOptions(within(row).getByRole("combobox", { name: "Command 1 type" }), "message");
    expect(within(row).queryByRole("textbox", { name: "Command 1 description" })).toBeNull();
    expect(within(row).queryByRole("button", { name: "Add option to command 1" })).toBeNull();
    expect(latest.commands[0].type).toBe("message");
  });

  it("shows a command's and an option's errors on their fields", () => {
    render(
      <Harness
        initial={discord([slash({ options: [option()] })])}
        errors={{ "commands.0.name": "Bad name.", "commands.0.options.0.name": "Bad option.", commands: "Too many." }}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Command 1 name" })).toHaveAccessibleDescription(/Bad name/);
    expect(screen.getByRole("textbox", { name: "Command 1 option 1 name" })).toHaveAccessibleDescription(/Bad option/);
    expect(within(screen.getByRole("group", { name: "Commands" })).getByText("Too many.")).toBeInTheDocument();
  });
});
