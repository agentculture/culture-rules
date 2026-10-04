import type { Actor, ActorKind } from "../api/actors";

export type KindFilter = "all" | ActorKind;

/** The board's filter pills, in order; `daemon` is added so every kind is reachable by name. */
export const FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "agent", label: "Agents" },
  { value: "human", label: "Humans" },
  { value: "service", label: "Services" },
  { value: "daemon", label: "Daemons" },
  { value: "runner", label: "Runners" },
  { value: "robot", label: "Robots" },
  { value: "app", label: "Apps" },
];

export const KINDS: ActorKind[] = ["agent", "human", "service", "daemon", "runner", "robot", "app"];

export const kindOfFilter = (filter: KindFilter): ActorKind | null => (filter === "all" ? null : filter);

export function filterActors(actors: Actor[], filter: KindFilter): Actor[] {
  const kind = kindOfFilter(filter);
  return kind ? actors.filter((a) => a.kind === kind) : actors;
}

/** The repo's own config file for `repo` actors (the board's mono path), or the db record. */
export function configSourceText(actor: Actor): string {
  if (actor.config_source === "repo") {
    return actor.repo ? `${actor.repo}/culture.yaml` : "no repo set";
  }
  return "db record";
}

export function parseCapabilities(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const cap = part.trim();
    if (cap) seen.add(cap);
  }
  return [...seen];
}
