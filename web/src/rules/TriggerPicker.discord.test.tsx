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
    expect(screen.getByText(/matched against the roles of whoever posted or reacted/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Users"));
    await user.clear(screen.getByLabelText("Roles"));
    await user.click(screen.getByRole("checkbox", { name: "Include messages from bots" }));
    expect(saved()).toEqual(START);
  });

  it("will not save Only these channels with none picked (PR #96 review)", async () => {
    const user = userEvent.setup();
    render(
      <form aria-label="rule">
        <Harness />
        <button type="submit">Save</button>
      </form>,
    );
    const group = await screen.findByRole("group", { name: "Channels" });
    await user.click(within(group).getByRole("radio", { name: "Only these channels" }));
    const general = await within(group).findByRole("checkbox", { name: "#general" });
    expect(general).toBeRequired();
    expect(within(group).getByText("Pick at least one channel.")).toBeInTheDocument();
    expect((screen.getByRole("form", { name: "rule" }) as HTMLFormElement).checkValidity()).toBe(false);
    await user.click(general);
    expect(general).not.toBeRequired();
    expect((screen.getByRole("form", { name: "rule" }) as HTMLFormElement).checkValidity()).toBe(true);
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.message.created", channels: ["101"] } });
  });
});

describe("discord.reaction.added", () => {
  const REACTOR: Actor = { ...BOT, params: { ...BOT.params, events: ["discord.message.created", "discord.reaction.added"] } };
  const REACTION: Trigger = { kind: "event", params: { type: "discord.reaction.added" } };

  function ReactionHarness() {
    const [value, setValue] = useState<Trigger>(REACTION);
    return (
      <>
        <TriggerPicker value={value} actors={[REACTOR]} onChange={setValue} loadDiscordTargets={async () => TARGETS} />
        <output data-testid="saved">{JSON.stringify(value)}</output>
      </>
    );
  }

  it("offers an emoji field (unicode or a custom name:id) and saves it as a list", async () => {
    const user = userEvent.setup();
    render(<ReactionHarness />);
    await user.type(await screen.findByLabelText("Emoji"), "📚, save:99");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.reaction.added", emoji: ["📚", "save:99"] } });
    await user.clear(screen.getByLabelText("Emoji"));
    expect(saved()).toEqual(REACTION);
  });

  it("shows no emoji field on a message trigger", async () => {
    render(<Harness />);
    await screen.findByRole("group", { name: "Channels" });
    expect(screen.queryByLabelText("Emoji")).not.toBeInTheDocument();
  });
});

describe("switching the event type", () => {
  it("drops a hidden emoji filter when the trigger leaves reactions (review of PR #102)", async () => {
    const REACTOR: Actor = { ...BOT, params: { ...BOT.params, events: ["discord.message.created", "discord.reaction.added"] } };
    const start: Trigger = { kind: "event", params: { type: "discord.reaction.added", emoji: ["📚"], users: ["ori"] } };
    function SwitchHarness() {
      const [value, setValue] = useState<Trigger>(start);
      return (
        <>
          <TriggerPicker value={value} actors={[REACTOR]} onChange={setValue} loadDiscordTargets={async () => TARGETS} />
          <output data-testid="saved">{JSON.stringify(value)}</output>
        </>
      );
    }
    const user = userEvent.setup();
    render(<SwitchHarness />);
    await user.selectOptions(screen.getByLabelText("Event"), "discord.message.created");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.message.created", users: ["ori"] } });
  });
});

describe("discord.command.invoked (d12)", () => {
  const COMMANDER: Actor = {
    ...BOT,
    params: { ...BOT.params, events: ["discord.message.created", "discord.command.invoked"] },
  };
  const INVOKED: Trigger = { kind: "event", params: { type: "discord.command.invoked" } };

  function CommandHarness({ start = INVOKED }: Readonly<{ start?: Trigger }>) {
    const [value, setValue] = useState<Trigger>(start);
    return (
      <>
        <TriggerPicker value={value} actors={[COMMANDER]} onChange={setValue} loadDiscordTargets={async () => TARGETS} />
        <output data-testid="saved">{JSON.stringify(value)}</output>
      </>
    );
  }

  it("saves one command name as a string and several as a list, and omits it when emptied", async () => {
    const user = userEvent.setup();
    render(<CommandHarness />);
    const field = await screen.findByLabelText("Command");
    await user.type(field, "save");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.command.invoked", command: "save" } });
    await user.type(field, ", Save to library");
    expect(saved()).toEqual({
      kind: "event",
      params: { type: "discord.command.invoked", command: ["save", "Save to library"] },
    });
    await user.clear(field);
    expect(saved()).toEqual(INVOKED);
  });

  it("shows a stored command name or list", async () => {
    const { unmount } = render(<CommandHarness start={{ kind: "event", params: { type: "discord.command.invoked", command: "save" } }} />);
    expect(await screen.findByLabelText("Command")).toHaveValue("save");
    unmount();
    render(<CommandHarness start={{ kind: "event", params: { type: "discord.command.invoked", command: ["save", "find"] } }} />);
    expect(await screen.findByLabelText("Command")).toHaveValue("save, find");
  });

  it("shows no command field on a message or reaction trigger", async () => {
    render(<Harness />);
    await screen.findByRole("group", { name: "Channels" });
    expect(screen.queryByLabelText("Command")).not.toBeInTheDocument();
  });

  it("drops a hidden command filter when the trigger leaves commands", async () => {
    const user = userEvent.setup();
    render(<CommandHarness start={{ kind: "event", params: { type: "discord.command.invoked", command: "save", users: ["ori"] } }} />);
    await user.selectOptions(screen.getByLabelText("Event"), "discord.message.created");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.message.created", users: ["ori"] } });
  });
});

describe("discord.button.clicked", () => {
  const CLICKER: Actor = { ...BOT, params: { ...BOT.params, events: ["discord.message.created", "discord.button.clicked"] } };
  const start: Trigger = { kind: "event", params: { type: "discord.button.clicked" } };
  function ClickHarness({ initial = start }: Readonly<{ initial?: Trigger }>) {
    const [value, setValue] = useState<Trigger>(initial);
    return (
      <>
        <TriggerPicker value={value} actors={[CLICKER]} onChange={setValue} loadDiscordTargets={async () => TARGETS} />
        <output data-testid="saved">{JSON.stringify(value)}</output>
      </>
    );
  }

  it("offers a button id field and saves one id as a string, several as a list", async () => {
    const user = userEvent.setup();
    render(<ClickHarness />);
    await user.type(await screen.findByLabelText("Button id"), "merge:");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.button.clicked", custom_id: "merge:" } });
    await user.type(screen.getByLabelText("Button id"), ", skip:");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.button.clicked", custom_id: ["merge:", "skip:"] } });
  });

  it("shows no button id field on another event, and clears it on a type change", async () => {
    const user = userEvent.setup();
    render(<ClickHarness initial={{ kind: "event", params: { type: "discord.button.clicked", custom_id: "merge:" } }} />);
    await user.selectOptions(await screen.findByLabelText("Event"), "discord.message.created");
    expect(screen.queryByLabelText("Button id")).not.toBeInTheDocument();
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.message.created" } });
  });
});
