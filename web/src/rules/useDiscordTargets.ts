import { useEffect, useState } from "react";
import type { DiscordTargets } from "../api/actors";

export type LoadTargets = (actorId: string) => Promise<DiscordTargets>;

/**
 * The servers and channels a Discord app actor's bot can see (`GET /actors/{id}/discord/targets`).
 * `targets` is null while loading (or without an actor); `failed` when the call errors. Reloads
 * only when the actor (or the loader) changes. Shared by the trigger and action pickers.
 */
export function useDiscordTargets(actorId: string, load: LoadTargets, onLoaded?: (t: DiscordTargets) => void) {
  const [targets, setTargets] = useState<DiscordTargets | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setTargets(null);
    setFailed(false);
    if (!actorId) return undefined;
    load(actorId)
      .then((t) => {
        if (!live) return;
        setTargets(t);
        onLoaded?.(t);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when the actor changes
  }, [actorId, load]);
  return { targets, failed };
}
