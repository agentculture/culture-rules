import ActorsBoard from "../actors/ActorsBoard";

/**
 * The Actors tab — the 'Chosen — Actors' board lives in `src/actors/`; this
 * is its route (selection rides on `?id=`).
 */
export function Actors() {
  return <ActorsBoard />;
}

export default Actors;
