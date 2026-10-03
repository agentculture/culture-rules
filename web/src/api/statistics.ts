import { items } from "./client";
import type { MachineStatus } from "./types";

export type { MachineStatus };

/**
 * `GET /machines/status`: per enrolled machine, whether it is online, its
 * load, the steps it runs and its queue depth (api/openapi.json). A failure
 * throws like every other call; the board then derives what it can from
 * `/runs` and lists the failure (see statistics-view.ts `buildLanes`).
 */
export const getMachineStatuses = (signal?: AbortSignal) =>
  items<MachineStatus>("/machines/status", signal);
