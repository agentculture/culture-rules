import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor, DiscordTargets } from "../api/actors";
import type { Action } from "../api/types";
import ActionPicker, { ACTION_KINDS, actionProblem, blankAction } from "./ActionPicker";

const BOT: Actor = {
  id: "discord-bot",
  name: "Community bot",
  kind: "app",
  enabled: true,
  params: { surface: "discord", events: [], actions: ["discord.message", "discord.read"], connection: {} },
};
const TARGETS: DiscordTargets = {
  guilds: [{ id: "10", name: "Lab", channels: [{ id: "101", name: "general", visible: true }] }],
};

function Harness() {
  const [value, setValue] = useState<Action>(blankAction());
  return (
    <>
      <ActionPicker value={value} actors={[BOT]} triggerType="discord.message.created" onChange={setValue}
        loadDiscordTargets={async () => TARGETS} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

describe("discord.read", () => {
  it("is catalogued with actor, channel and message required and guild optional", () => {
    const p = ACTION_KINDS["discord.read"].params;
    expect(p.actor.required).toBe(true);
    expect(p.channel.required).toBe(true);
    expect(p.message.required).toBe(true);
    expect(p.guild.required).toBeFalsy();
    expect(actionProblem({ kind: "discord.read", params: { actor: "a", channel: "1" } })).toBe("empty");
    expect(actionProblem({ kind: "discord.read", params: { actor: "a", channel: "1", message: "2" } })).toBeNull();
  });

  it("is offered for a bot that declares it, with the server and channel pickers and a message field", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(within(screen.getByLabelText("What happens")).getByRole("option", { name: /Read a Discord message/ })).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.read");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await user.selectOptions(await screen.findByLabelText("Channel"), "101");
    expect(screen.getByLabelText("Message")).toBeTruthy();
    await user.type(screen.getByLabelText("Message"), "555");
    expect(JSON.parse(screen.getByTestId("saved").textContent ?? "{}")).toEqual({
      kind: "discord.read",
      params: { actor: "discord-bot", guild: "10", channel: "101", message: "555" },
    });
  });
});

describe("discord.react", () => {
  const REACTOR: Actor = { ...BOT, params: { ...BOT.params, actions: ["discord.read", "discord.react"] } };

  function ReactHarness() {
    const [value, setValue] = useState<Action>(blankAction());
    return (
      <>
        <ActionPicker value={value} actors={[REACTOR]} triggerType="discord.reaction.added" onChange={setValue}
          loadDiscordTargets={async () => TARGETS} />
        <output data-testid="saved">{JSON.stringify(value)}</output>
      </>
    );
  }

  it("is catalogued with actor, channel and message required, and an emoji to add or remove", () => {
    const p = ACTION_KINDS["discord.react"].params;
    expect([p.actor, p.channel, p.message].every((s) => s.required)).toBe(true);
    expect(p.guild.required).toBeFalsy();
    expect(p.remove.type).toBe("list");
    expect(ACTION_KINDS["discord.react"].anyOf).toEqual([["emoji", "remove"]]);
    expect(actionProblem({ kind: "discord.react", params: { actor: "a", channel: "1", message: "2" } })).toBe("empty");
    expect(
      actionProblem({ kind: "discord.react", params: { actor: "a", channel: "1", message: "2", remove: ["⏳"] } }),
    ).toBeNull();
  });

  it("picks the channel from what the bot sees and takes an emoji", async () => {
    const user = userEvent.setup();
    render(<ReactHarness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.react");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await user.selectOptions(await screen.findByLabelText("Channel"), "101");
    await user.type(screen.getByLabelText("Message"), "555");
    await user.type(screen.getByLabelText("Emoji"), "✅");
    expect(JSON.parse(screen.getByTestId("saved").textContent ?? "{}")).toEqual({
      kind: "discord.react",
      params: { actor: "discord-bot", guild: "10", channel: "101", message: "555", emoji: "✅" },
    });
  });
});

describe("Release 3 kinds", () => {
  it("catalogues discord.reply with actor, event_id and text required", () => {
    const p = ACTION_KINDS["discord.reply"].params;
    expect([p.actor, p.event_id, p.text].every((s) => s.required)).toBe(true);
    expect(actionProblem({ kind: "discord.reply", params: { actor: "a", event_id: "e" } })).toBe("empty");
    expect(actionProblem({ kind: "discord.reply", params: { actor: "a", event_id: "e", text: "Saved" } })).toBeNull();
  });

  it("catalogues discord.sweep with the channel and a cursor key required, the rest optional", () => {
    const p = ACTION_KINDS["discord.sweep"].params;
    expect([p.actor, p.channel, p.cursor_key].every((s) => s.required)).toBe(true);
    expect([p.guild, p.limit, p.include_bots, p.backfill].some((s) => s.required)).toBe(false);
    expect(actionProblem({ kind: "discord.sweep", params: { actor: "a", channel: "1", cursor_key: "k", limit: "x" } })).toBe("invalid_value");
  });

  it("picks the sweep's channel from what the bot sees", async () => {
    const SWEEPER: Actor = { ...BOT, params: { ...BOT.params, actions: ["discord.sweep"] } };
    function SweepHarness() {
      const [value, setValue] = useState<Action>(blankAction());
      return (
        <>
          <ActionPicker value={value} actors={[SWEEPER]} onChange={setValue} loadDiscordTargets={async () => TARGETS} />
          <output data-testid="saved">{JSON.stringify(value)}</output>
        </>
      );
    }
    const user = userEvent.setup();
    render(<SweepHarness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.sweep");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await user.selectOptions(await screen.findByLabelText("Channel"), "101");
    await user.type(screen.getByLabelText("Cursor key"), "lib");
    expect(JSON.parse(screen.getByTestId("saved").textContent ?? "{}")).toEqual({
      kind: "discord.sweep",
      params: { actor: "discord-bot", guild: "10", channel: "101", cursor_key: "lib" },
    });
  });
});

describe("discord.message buttons", () => {
  it("adds, edits and removes button rows, at most five", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.message");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await user.click(screen.getByRole("button", { name: "Add button" }));
    await user.type(screen.getByLabelText("Button 1 label"), "Merge");
    await user.selectOptions(screen.getByLabelText("Button 1 style"), "success");
    await user.type(screen.getByLabelText("Button 1 custom id"), "merge:1");
    const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "{}").params.buttons;
    expect(saved()).toEqual([{ label: "Merge", style: "success", custom_id: "merge:1" }]);
    for (let i = 0; i < 4; i++) await user.click(screen.getByRole("button", { name: "Add button" }));
    expect(screen.getByRole("button", { name: "Add button" })).toBeDisabled();
    for (let i = 5; i > 0; i--) await user.click(screen.getByRole("button", { name: `Remove button ${i}` }));
    expect(saved()).toBeUndefined();
  });

  it("saves a button's value as a JSON object and refuses invalid JSON", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.message");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await user.click(screen.getByRole("button", { name: "Add button" }));
    await user.type(screen.getByLabelText("Button 1 label"), "Merge");
    await user.type(screen.getByLabelText("Button 1 custom id"), "merge");
    const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "{}").params.buttons;
    const box = screen.getByLabelText("Button 1 value");
    await user.type(box, '{{"number": 7');
    expect(screen.getByRole("alert")).toHaveTextContent("Button 1 value must be a JSON object");
    expect(saved()).toEqual([{ label: "Merge", style: "primary", custom_id: "merge" }]);
    await user.type(box, "}");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(saved()).toEqual([{ label: "Merge", style: "primary", custom_id: "merge", value: { number: 7 } }]);
    await user.type(box, "x");
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(saved()[0].value).toEqual({ number: 7 });
    await user.clear(box);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(saved()).toEqual([{ label: "Merge", style: "primary", custom_id: "merge" }]);
    await user.type(box, "[[1]");
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(saved()).toEqual([{ label: "Merge", style: "primary", custom_id: "merge" }]);
  });

  it("shows a stored value as JSON", () => {
    const action: Action = {
      kind: "discord.message",
      params: { actor: "discord-bot", buttons: [{ label: "Merge", style: "success", custom_id: "merge", value: { n: 1 } }] },
    };
    render(<ActionPicker value={action} actors={[BOT]} triggerType="discord.message.created" onChange={() => {}}
      loadDiscordTargets={async () => TARGETS} />);
    expect(JSON.parse((screen.getByLabelText("Button 1 value") as HTMLTextAreaElement).value)).toEqual({ n: 1 });
  });
});
