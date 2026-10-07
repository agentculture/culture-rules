import { useEffect, useState, type CSSProperties } from "react";
import { getDescription, type Description as DescriptionDoc } from "../api/describe";
import "./description.css";

/**
 * The stored definition's plain description (d19), fetched from the API, which
 * builds it from the config alone. It refetches when `stamp` (the stored
 * document) changes; a failed fetch shows nothing — the description is an
 * aid beside the visual flow, never in its way.
 */
export function useDescription(
  noun: "rules" | "workflows",
  id: string | undefined,
  stamp: unknown,
): DescriptionDoc | null {
  const [doc, setDoc] = useState<DescriptionDoc | null>(null);
  const key = JSON.stringify(stamp ?? null);
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    getDescription(noun, id, controller.signal)
      .then(setDoc)
      .catch(() => {
        if (!controller.signal.aborted) setDoc(null);
      });
    return () => controller.abort();
  }, [noun, id, key]);
  return doc && doc.id === id ? doc : null;
}

/** "In words": a compact numbered list — `When …`, `If …`, or `1 quiet — wait 300 s`. */
export function Description({
  doc,
  stale = false,
}: Readonly<{ doc: DescriptionDoc | null; stale?: boolean }>) {
  if (!doc || doc.entries.length === 0) return null;
  return (
    <section className="describe" aria-label="In words" data-describe={doc.kind}>
      <h2 className="describe__title">
        In words
        {stale ? <span className="describe__note"> · the saved version</span> : null}
      </h2>
      <ol className="describe__list">
        {doc.entries.map((e, i) => (
          <li
            key={`${i}-${e.label}`}
            className="describe__line"
            style={{ "--describe-depth": e.depth } as CSSProperties}
          >
            <span className="describe__label">{e.label}</span>
            {e.step ? <span className="describe__step">{e.step}</span> : null}
            {e.text ? <span className="describe__text">{e.text}</span> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

export default Description;
