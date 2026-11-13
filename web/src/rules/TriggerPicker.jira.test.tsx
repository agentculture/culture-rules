import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import type { Trigger } from "../api/types";
import TriggerPicker from "./TriggerPicker";

/* Jira trigger filter: users (accountIds); discord keeps its own fields, others show none. */

const JIRA: Actor = {
  id: "jira",
  name: "Jira",
  kind: "app",
  enabled: true,
  params: { surface: "jira", events: ["jira.issue.created"], actions: [], connection: {} },
};
const BOT: Actor = {
  id: "discord-bot",
  name: "Bot",
  kind: "app",
  enabled: true,
  params: { surface: "discord", events: ["discord.message.created"], actions: [], connection: {} },
};

function Harness({ start }: Readonly<{ start: Trigger }>) {
  const [value, setValue] = useState<Trigger>(start);
  return (
    <>
      <TriggerPicker value={value} actors={[JIRA, BOT]} onChange={setValue} loadDiscordTargets={async () => ({ guilds: [] })} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}
const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Trigger;

describe("Jira trigger filter", () => {
  it("writes params.users and removes it when emptied", async () => {
    const user = userEvent.setup();
    render(<Harness start={{ kind: "event", params: { type: "jira.issue.created" } }} />);
    const field = screen.getByRole("textbox", { name: /Users/ });
    await user.type(field, "abc, def");
    expect(saved()).toEqual({ kind: "event", params: { type: "jira.issue.created", users: ["abc", "def"] } });
    await user.clear(field);
    expect(saved()).toEqual({ kind: "event", params: { type: "jira.issue.created" } });
    expect(screen.queryByRole("checkbox", { name: /bots/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Channels" })).not.toBeInTheDocument();
  });

  it("loads a stored users list", () => {
    render(<Harness start={{ kind: "event", params: { type: "jira.issue.created", users: ["abc"] } }} />);
    expect(screen.getByRole("textbox", { name: /Users/ })).toHaveValue("abc");
  });

  it("leaves discord and other triggers alone", async () => {
    render(<Harness start={{ kind: "event", params: { type: "discord.message.created" } }} />);
    expect(await screen.findByRole("group", { name: "Channels" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /bots/ })).toBeInTheDocument();
  });

  it("shows no Users field for a non-app event", () => {
    render(<Harness start={{ kind: "event", params: { type: "" } }} />);
    expect(screen.queryByRole("textbox", { name: /Users/ })).not.toBeInTheDocument();
  });

  it("drops jira users when the surface changes to Discord", async () => {
    const user = userEvent.setup();
    render(<Harness start={{ kind: "event", params: { type: "jira.issue.created", users: ["abc"], include_self: true } }} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "discord-bot");
    expect(saved()).toEqual({ kind: "event", params: { type: "", include_self: true } });
  });

  it("drops discord channels and users when the surface changes to Jira", async () => {
    const user = userEvent.setup();
    render(<Harness start={{ kind: "event", params: { type: "discord.message.created", channels: ["1"], users: ["u"], roles: ["r"] } }} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "jira");
    expect(saved()).toEqual({ kind: "event", params: { type: "" } });
  });

  it("keeps users between two events of one app", async () => {
    const user = userEvent.setup();
    const two: Actor = { ...BOT, params: { ...BOT.params, events: ["discord.message.created", "discord.reaction.added"] } };
    function Two() {
      const [value, setValue] = useState<Trigger>({ kind: "event", params: { type: "discord.message.created", users: ["u"] } });
      return (
        <>
          <TriggerPicker value={value} actors={[two]} onChange={setValue} loadDiscordTargets={async () => ({ guilds: [] })} />
          <output data-testid="saved">{JSON.stringify(value)}</output>
        </>
      );
    }
    render(<Two />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Event" }), "discord.reaction.added");
    expect(saved()).toEqual({ kind: "event", params: { type: "discord.reaction.added", users: ["u"] } });
  });
});
