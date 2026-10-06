import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ACTORS, WORKFLOWS } from "../fixtures/rules-fixture";
import type { Action } from "../api/types";
import ActionPicker, { actionProblem, blankAction } from "./ActionPicker";

function Harness({
  start,
  trigger = "github.pr.opened",
  workflow,
}: Readonly<{ start?: Action; trigger?: string; workflow?: (typeof WORKFLOWS)[number] }>) {
  const [value, setValue] = useState<Action>(start ?? blankAction());
  return (
    <>
      <ActionPicker value={value} actors={ACTORS} triggerType={trigger} workflow={workflow} onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Action;
const kinds = () =>
  within(screen.getByLabelText("What happens")).getAllByRole("option").map((o) => o.getAttribute("value"));

describe("kinds offered", () => {
  it("offers only kinds some enabled actor supports, plus the mesh message and noop", () => {
    render(<Harness />);
    // github-app (enabled) declares github.comment; ci-runner runs machine.command;
    // jira.comment is declared only by the DISABLED discord app; http.call has no actor.
    expect(kinds()).toEqual(["noop", "message", "github.comment", "machine.command"]);
  });

  it("offers http.call once an enabled runner allows a host", () => {
    const actors = structuredClone(ACTORS);
    (actors[2].params as Record<string, unknown>).http = { allow: ["api.example.com"] };
    render(<ActionPicker value={blankAction()} actors={actors} onChange={() => {}} />);
    expect(
      within(screen.getByLabelText("What happens"))
        .getAllByRole("option")
        .map((o) => o.getAttribute("value")),
    ).toContain("http.call");
  });
});

describe("actor select", () => {
  it("lists only the actors that support the picked kind", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "machine.command");
    const options = within(screen.getByLabelText("Actor")).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(options).toEqual(["", "ci-runner"]);
    await user.selectOptions(screen.getByLabelText("What happens"), "github.comment");
    const gh = within(screen.getByLabelText("Actor")).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(gh).toEqual(["", "github-app"]);
  });
});

describe("typed params and mappings", () => {
  it("saves a mapped param as the reference string and renders a chip showing it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "github.comment");
    await user.selectOptions(screen.getByLabelText("Actor"), "github-app");
    await user.type(screen.getByLabelText("Repo"), "acme/app");
    await user.selectOptions(screen.getByLabelText("Map Number"), "trigger.data.number");
    await user.type(screen.getByLabelText("Body"), "Thanks!");
    expect(saved()).toEqual({
      kind: "github.comment",
      params: { actor: "github-app", repo: "acme/app", number: "trigger.data.number", body: "Thanks!" },
    });
    const chip = screen.getByTestId("chip-number");
    expect(chip).toHaveTextContent("trigger.data.number");
    expect(screen.queryByRole("textbox", { name: "Number" })).not.toBeInTheDocument();
    expect(actionProblem(saved())).toBeNull();
    // un-mapping returns to a literal field
    await user.click(screen.getByRole("button", { name: "Use a fixed value for Number" }));
    expect(screen.getByLabelText("Number")).toBeInTheDocument();
    expect(saved().params).not.toHaveProperty("number", "trigger.data.number");
  });

  it("offers workflow outputs of the rule's workflow and a custom path", async () => {
    const user = userEvent.setup();
    render(<Harness workflow={WORKFLOWS[0]} />);
    await user.selectOptions(screen.getByLabelText("What happens"), "github.comment");
    const map = screen.getByLabelText("Map Body");
    const values = within(map).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(values).toContain("workflow.outputs.image");
    expect(values).toContain("trigger.data.number");
    await user.selectOptions(map, "workflow.outputs.image");
    expect(saved().params?.body).toBe("workflow.outputs.image");
    await user.selectOptions(screen.getByLabelText("Map Repo"), "__custom__");
    await user.type(screen.getByLabelText("Path for Repo"), "trigger.data.repository.full_name");
    expect(saved().params?.repo).toBe("trigger.data.repository.full_name");
  });

  it("writes a machine.command's command and a typed args mapping", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("What happens"), "machine.command");
    await user.selectOptions(screen.getByLabelText("Actor"), "ci-runner");
    await user.selectOptions(screen.getByLabelText("Command"), "disk-free");
    await user.type(screen.getByLabelText("path"), "/data");
    expect(saved()).toEqual({
      kind: "machine.command",
      params: { actor: "ci-runner", command: "disk-free", args: { path: "/data" } },
    });
    await user.selectOptions(screen.getByLabelText("Map path"), "trigger.data.number");
    expect(saved().params?.args).toEqual({ path: "trigger.data.number" });
  });
});

describe("validation", () => {
  it("names a missing actor or required param by code", () => {
    expect(actionProblem({ kind: "github.comment", params: {} })).toBe("no_actor_port");
    expect(actionProblem({ kind: "github.comment", params: { actor: "github-app", repo: "a/b" } })).toBe("empty");
    expect(actionProblem({ kind: "message", params: { channel: "#x", text: "hi" } })).toBeNull();
    expect(actionProblem({ kind: "noop" })).toBeNull();
    expect(actionProblem({ kind: "mesh.message", name: "Notify" })).toBeNull();
    expect(actionProblem({ kind: "github.comment", params: { actor: "g", repo: "a/b", number: "x1", body: "b" } })).toBe(
      "invalid_value",
    );
  });
});

describe("existing action", () => {
  it("preselects the kind, actor, literals and mapped chips", () => {
    render(
      <Harness
        start={{
          kind: "github.comment",
          name: "Reply",
          params: { actor: "github-app", repo: "acme/app", number: "trigger.data.number", body: "hi" },
        }}
      />,
    );
    expect(screen.getByLabelText("What happens")).toHaveValue("github.comment");
    expect(screen.getByLabelText("Actor")).toHaveValue("github-app");
    expect(screen.getByLabelText("Repo")).toHaveValue("acme/app");
    expect(screen.getByTestId("chip-number")).toHaveTextContent("trigger.data.number");
    expect(screen.getByLabelText("Action label")).toHaveValue("Reply");
  });

  it("keeps an action kind the editor does not know as it is", () => {
    render(<Harness start={{ kind: "code.run", name: "Clean" }} />);
    expect(screen.getByText(/“code.run” action the editor cannot change/)).toBeInTheDocument();
    expect(saved()).toEqual({ kind: "code.run", name: "Clean" });
  });
});

describe("mesh message and Discord message are separate kinds", () => {
  const BOT = {
    id: "discord-bot",
    name: "Discord bot",
    kind: "app" as const,
    enabled: true,
    params: {
      surface: "discord" as const,
      events: ["discord.message.created"],
      actions: ["discord.message"],
      connection: { bot_token: "grant:BOT" },
    },
  };
  const TWO = {
    guilds: [
      {
        id: "10",
        name: "Lab",
        channels: [
          { id: "103", name: "culture", visible: false },
          { id: "101", name: "general", visible: true },
        ],
      },
      { id: "20", name: "Other", channels: [{ id: "201", name: "x", visible: true }] },
    ],
  };

  function DiscordHarness({
    start,
    load,
  }: Readonly<{ start?: Action; load: (id: string) => Promise<typeof TWO> }>) {
    const [value, setValue] = useState<Action>(start ?? blankAction());
    return (
      <>
        <ActionPicker
          value={value}
          actors={[...ACTORS, BOT]}
          triggerType="discord.message.created"
          loadDiscordTargets={load}
          onChange={setValue}
        />
        <output data-testid="saved">{JSON.stringify(value)}</output>
      </>
    );
  }

  it("lists both kinds with their own labels; the mesh message has no actor", async () => {
    const user = userEvent.setup();
    render(<DiscordHarness load={async () => TWO} />);
    const labels = within(screen.getByLabelText("What happens"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(labels).toContain("Send a message on the mesh");
    expect(labels).toContain("Post a message on Discord");
    await user.selectOptions(screen.getByLabelText("What happens"), "message");
    expect(screen.queryByLabelText("Actor")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Channel"), "#ops");
    await user.type(screen.getByLabelText("Text"), "hi");
    expect(saved()).toEqual({ kind: "message", params: { channel: "#ops", text: "hi" } });
  });

  it("picks the server and then the channel from what the bot can see", async () => {
    const user = userEvent.setup();
    const asked: string[] = [];
    render(
      <DiscordHarness
        load={async (id) => {
          asked.push(id);
          return TWO;
        }}
      />,
    );
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.message");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    const server = await screen.findByLabelText("Server");
    expect(asked).toEqual(["discord-bot"]);
    await user.selectOptions(server, "10");
    const channel = screen.getByLabelText("Channel");
    const options = within(channel).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Choose a channel…",
      "#culture (the bot was not added)",
      "#general",
    ]);
    await user.selectOptions(channel, "101");
    await user.type(screen.getByLabelText("Text"), "hello");
    expect(saved()).toEqual({
      kind: "discord.message",
      params: { actor: "discord-bot", guild: "10", channel: "101", text: "hello" },
    });
    expect(actionProblem(saved())).toBeNull();
    // switching server clears a channel that belongs to the old one
    await user.selectOptions(screen.getByLabelText("Server"), "20");
    expect(saved().params).not.toHaveProperty("channel");
  });

  it("preselects the only server", async () => {
    const user = userEvent.setup();
    render(<DiscordHarness load={async () => ({ guilds: [TWO.guilds[1]] })} />);
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.message");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await screen.findByRole("option", { name: "#x" });
    expect(saved().params).toMatchObject({ guild: "20" });
  });

  it("maps the channel from the trigger to reply where the message came from", async () => {
    const user = userEvent.setup();
    render(<DiscordHarness load={async () => TWO} />);
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.message");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    await screen.findByLabelText("Server");
    await user.selectOptions(screen.getByLabelText("Map Channel"), "trigger.data.channel_id");
    expect(saved().params?.channel).toBe("trigger.data.channel_id");
    expect(screen.getByTestId("chip-channel")).toHaveTextContent("trigger.data.channel_id");
  });

  it("shows a stored message through a Discord actor as a Discord message", () => {
    render(
      <DiscordHarness
        load={async () => TWO}
        start={{ kind: "message", params: { actor: "discord-bot", channel: "101", text: "t" } }}
      />,
    );
    expect(screen.getByLabelText("What happens")).toHaveValue("discord.message");
    expect(screen.getByLabelText("Actor")).toHaveValue("discord-bot");
  });

  it("falls back to typing the channel id when the bot's channels cannot be loaded", async () => {
    const user = userEvent.setup();
    render(
      <DiscordHarness
        load={async () => {
          throw new Error("boom");
        }}
      />,
    );
    await user.selectOptions(screen.getByLabelText("What happens"), "discord.message");
    await user.selectOptions(screen.getByLabelText("Actor"), "discord-bot");
    expect(await screen.findByText(/could not load the bot's servers and channels/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Channel"), "101");
    expect(saved().params?.channel).toBe("101");
  });
});

describe("github.push and github.review_reply", () => {
  const fixer = (): typeof ACTORS => {
    const actors = structuredClone(ACTORS);
    (actors[0].params as Record<string, unknown>).actions = [
      "github.comment",
      "github.push",
      "github.review_reply",
    ];
    return actors;
  };
  function Fixer({ start }: Readonly<{ start?: Action }>) {
    const [value, setValue] = useState<Action>(start ?? blankAction());
    return (
      <>
        <ActionPicker value={value} actors={fixer()} triggerType="github.pr.opened" onChange={setValue} />
        <output data-testid="saved">{JSON.stringify(value)}</output>
      </>
    );
  }

  it("offers both once an app declares them", () => {
    render(<Fixer />);
    const offered = within(screen.getByLabelText("What happens"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(offered).toContain("github.push");
    expect(offered).toContain("github.review_reply");
  });

  it("requires the push's params and saves them typed", async () => {
    const user = userEvent.setup();
    render(<Fixer />);
    await user.selectOptions(screen.getByLabelText("What happens"), "github.push");
    expect(actionProblem(saved())).toBe("no_actor_port");
    await user.selectOptions(screen.getByLabelText("Actor"), "github-app");
    expect(actionProblem(saved())).toBe("empty");
    await user.type(screen.getByLabelText("Repo"), "acme/app");
    await user.type(screen.getByLabelText("Number"), "7");
    await user.type(screen.getByLabelText("Head branch"), "fix/x");
    await user.type(screen.getByLabelText("Expected head sha"), "a".repeat(40));
    await user.type(screen.getByLabelText("Commit sha"), "b".repeat(40));
    await user.type(screen.getByLabelText("Source"), "ci-runner");
    expect(actionProblem(saved())).toBeNull();
    expect(saved().params).toMatchObject({ number: 7, head_branch: "fix/x", repo: "acme/app" });
  });

  it("saves a review reply with a boolean resolve", async () => {
    const user = userEvent.setup();
    render(<Fixer />);
    await user.selectOptions(screen.getByLabelText("What happens"), "github.review_reply");
    await user.selectOptions(screen.getByLabelText("Actor"), "github-app");
    await user.type(screen.getByLabelText("Repo"), "acme/app");
    await user.type(screen.getByLabelText("Number"), "7");
    await user.type(screen.getByLabelText("Comment id"), "99");
    await user.type(screen.getByLabelText("Body"), "Fixed");
    await user.click(screen.getByRole("checkbox", { name: "Resolve the thread" }));
    expect(actionProblem(saved())).toBeNull();
    expect(saved().params).toMatchObject({ comment_id: 99, resolve: true });
    await user.click(screen.getByRole("checkbox", { name: "Resolve the thread" }));
    expect(saved().params).not.toHaveProperty("resolve");
  });

  it("rejects a non-boolean resolve", () => {
    expect(
      actionProblem({
        kind: "github.review_reply",
        params: { actor: "a", repo: "r", number: 1, comment_id: 2, body: "b", resolve: "yes" },
      }),
    ).toBe("invalid_value");
  });
});
