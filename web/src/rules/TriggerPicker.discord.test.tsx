import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor, DiscordTargets } from "../api/actors";
import type { Trigger } from "../api/types";
import TriggerPicker from "./TriggerPicker";

/* Discord trigger filters: channels (picked, or all), users, roles, include bots. */

const BOT: Actor = {
  id: "discord-bot",
  name: "Community bot",
  kind: "app",
  enabled: true,
  params: { surface: "discord", events: ["discord.message.created"], actions: ["discord.read"], connection: {} },
};
const TARGETS: DiscordTargets = {
  guilds: [
    {
      id: "10",
      name: "Lab",
      channels: [
        { id: "101", name: "general", visible: true },
        { id: "102", name: "secret", visible: false },
      ],
    },
  ],
};
const START: Trigger = { kind: "event", params: { type: "discord.message.created" } };

function Harness({
  start = START,
  load = async () => TARGETS,
}: Readonly<{ start?: Trigger; load?: (id: string) => Promise<DiscordTargets> }>) {
  const [value, setValue] = useState<Trigger>(start);
  return (
    <>
      <TriggerPicker value={value} actors={[BOT]} onChange={setValue} loadDiscordTargets={load} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}
const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Trigger;

describe("Discord trigger filters", () => {
  it("offers All channels by default and saves no channels key", async () => {
    render(<Harness />);
    const group = await screen.findByRole("group", { name: "Channels" });
    expect(within(group).getByRole("radio", { name: "All channels" })).toBeChecked();
    expect(saved()).toEqual(START);
  });

  it("is not shown for a non-Discord event", () => {
    render(<Harness start={{ kind: "event", params: { type: "" } }} />);
    expect(screen.queryByRole("group", { name: "Channels" })).not.toBeInTheDocument();
  });

  it("picks channels from the bot's targets and saves their ids", async () => {
    const asked: string[] = [];
    const user = userEvent.setup();
    render(<Harness load={async (id) => (asked.push(id), TARGETS)} />);
    const group = await screen.findByRole("group", { name: "Channels" });
    await user.click(within(group).getByRole("radio", { name: "Only these channels" }));
    await user.click(await within(group).findByRole("checkbox", { name: "#general" }));
    expect(asked).toEqual(["discord-bot"]);
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.message.created", channels: ["101"] } });
    expect(within(group).getByRole("checkbox", { name: /#secret/ })).toBeInTheDocument();
    await user.click(within(group).getByRole("radio", { name: "All channels" }));
    expect(saved()).toEqual(START);
  });

  it("keeps a stored channel id that is no longer listed", async () => {
    render(<Harness start={{ kind: "event", params: { type: "discord.message.created", channels: ["999"] } }} />);
    const group = await screen.findByRole("group", { name: "Channels" });
    expect(within(group).getByRole("radio", { name: "Only these channels" })).toBeChecked();
    expect(await within(group).findByRole("checkbox", { name: "999" })).toBeChecked();
  });

  it("falls back to typed ids when the targets cannot be loaded", async () => {
    const user = userEvent.setup();
    render(<Harness load={async () => Promise.reject(new Error("no"))} />);
    const group = await screen.findByRole("group", { name: "Channels" });
    await user.click(within(group).getByRole("radio", { name: "Only these channels" }));
    await waitFor(() => expect(within(group).getByLabelText("Channel ids")).toBeInTheDocument());
    await user.type(within(group).getByLabelText("Channel ids"), "5, 6");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.message.created", channels: ["5", "6"] } });
  });

  it("saves users, roles and include_bots, and drops them when emptied", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await screen.findByRole("group", { name: "Channels" });
    await user.type(screen.getByLabelText("Users"), "ori, 77");
    await user.type(screen.getByLabelText("Roles"), "900");
    await user.click(screen.getByRole("checkbox", { name: "Include messages from bots" }));
    expect(saved()).toEqual({
      kind: "event",
      params: { type: "discord.message.created", users: ["ori", "77"], roles: ["900"], include_bots: true },
    });
    expect(screen.getByText(/Roles apply from Release 2/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Users"));
    await user.clear(screen.getByLabelText("Roles"));
    await user.click(screen.getByRole("checkbox", { name: "Include messages from bots" }));
    expect(saved()).toEqual(START);
  });
});
