import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { CommandsEditor } from "./CommandsEditor";
import {
  commandsFrom,
  commandsParamFrom,
  inlineEvalWarning,
  validateCommands,
  type CommandDraft,
  type FormErrors,
} from "./app-config";

let latest: CommandDraft[] = [];

function Harness({ initial = [], errors }: Readonly<{ initial?: CommandDraft[]; errors?: FormErrors }>) {
  const [drafts, setDrafts] = useState(initial);
  latest = drafts;
  return <CommandsEditor value={drafts} onChange={setDrafts} errors={errors} />;
}

describe("CommandsEditor", () => {
  it("adds a command with a name, argv tokens, typed params and a timeout", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Add command" }));
    const cmd = screen.getByRole("group", { name: "Command 1" });
    await user.type(within(cmd).getByRole("textbox", { name: "Command 1 name" }), "echo");
    await user.type(within(cmd).getByRole("textbox", { name: "Command 1 token 1" }), "echo");
    await user.click(within(cmd).getByRole("button", { name: "Add token to command 1" }));
    await user.type(within(cmd).getByRole("textbox", { name: "Command 1 token 2" }), "{{msg}");
    await user.click(within(cmd).getByRole("button", { name: "Add parameter to command 1" }));
    await user.type(within(cmd).getByRole("textbox", { name: "Command 1 parameter 1 name" }), "msg");
    await user.selectOptions(within(cmd).getByRole("combobox", { name: "Command 1 parameter 1 type" }), "string");
    await user.type(within(cmd).getByRole("textbox", { name: "Command 1 timeout" }), "30");
    expect(latest).toEqual([
      { name: "echo", argv: ["echo", "{msg}"], params: [{ name: "msg", type: "string" }], timeout: "30" },
    ]);
    expect(commandsParamFrom(latest)).toEqual({
      echo: { argv: ["echo", "{msg}"], params: { msg: "string" }, timeout: 30 },
    });
    expect(validateCommands(latest)).toEqual({});
  });

  it("removes tokens, params and commands", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        initial={[
          { name: "a", argv: ["x", "y"], params: [{ name: "p", type: "int" }], timeout: "" },
          { name: "b", argv: ["z"], params: [], timeout: "" },
        ]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Remove token 2 of command 1" }));
    await user.click(screen.getByRole("button", { name: "Remove parameter 1 of command 1" }));
    expect(latest[0]).toMatchObject({ argv: ["x"], params: [] });
    await user.click(screen.getByRole("button", { name: "Remove command 2" }));
    expect(latest).toHaveLength(1);
  });

  it("warns, without blocking, when the template evaluates inline code", async () => {
    render(<Harness initial={[{ name: "sh", argv: ["/bin/sh", "-c", "{script}"], params: [{ name: "script", type: "string" }], timeout: "" }]} />);
    expect(screen.getByRole("note")).toHaveTextContent(/evaluates inline code/);
    expect(validateCommands(latest)).toEqual({});
  });

  it("shows field errors on the field", () => {
    render(<Harness initial={[{ name: "", argv: [""], params: [], timeout: "" }]} errors={{ "0.name": "A command needs a name." }} />);
    const name = screen.getByRole("textbox", { name: "Command 1 name" });
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("A command needs a name.");
  });
});

describe("commands model", () => {
  it("round-trips params.commands", () => {
    const commands = { echo: { argv: ["echo", "{msg}"], params: { msg: "string" }, timeout: 30 }, ls: { argv: ["ls"] } };
    expect(commandsParamFrom(commandsFrom({ commands }))).toEqual(commands);
  });

  it("flags undeclared placeholders, duplicates, blanks and bad timeouts", () => {
    const errs = validateCommands([
      { name: "a", argv: ["echo", "{x}"], params: [], timeout: "0" },
      { name: "a", argv: [""], params: [], timeout: "" },
    ]);
    expect(Object.keys(errs).sort()).toEqual(["0.argv", "0.timeout", "1.argv", "1.name"]);
  });

  it("recognises shells and interpreters told to evaluate code", () => {
    expect(inlineEvalWarning(["bash", "-c", "{s}"])).toMatch(/inline/);
    expect(inlineEvalWarning(["/usr/bin/python3", "-c", "print(1)"])).toMatch(/inline/);
    expect(inlineEvalWarning(["node", "--eval", "1"])).toMatch(/inline/);
    expect(inlineEvalWarning(["echo", "-c", "x"])).toBeNull();
    expect(inlineEvalWarning(["python3", "script.py"])).toBeNull();
  });
});
