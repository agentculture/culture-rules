import { ApiError } from "../api/client";
import { fixLabel, guidanceFor, type Fix } from "../api/guidance";

interface Props {
  /** The failure; only its code is read, never its message. */
  error?: ApiError | { code: string };
  /** Alternative to `error`: a bare code. */
  code?: string;
  /** Nested errors[].code values, for a more specific message. */
  nested?: readonly string[];
  /** Handles a chosen fix; with no handler the notice shows the message only. */
  onFix?: (fix: Fix) => void;
}

/** A failure in plain language with typed fix buttons. Never renders raw error text. */
export default function GuidedNotice({ error, code, nested, onFix }: Readonly<Props>) {
  const guidance = guidanceFor(error?.code ?? code ?? "", nested);
  return (
    <div className="guided-notice" role="alert">
      <p className="guided-notice__message">{guidance.message}</p>
      {onFix && (
        <div className="guided-notice__fixes">
          {guidance.fixes.map((fix, i) => (
            <button key={`${fix.kind}-${i}`} type="button" className="guided-notice__fix" onClick={() => onFix(fix)}>
              {fixLabel(fix)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
