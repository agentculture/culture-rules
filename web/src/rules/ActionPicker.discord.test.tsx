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
