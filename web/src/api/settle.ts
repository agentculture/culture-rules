/**
 * Load several API calls side by side and apply whatever came back, without a
 * floating promise: `Promise.allSettled` itself never rejects, but applying
 * the results can throw (a reason that cannot be described, a bad shape), and
 * that failure must reach the view as an error, not vanish as an unhandled
 * rejection that leaves the tab loading forever.
 */

/** A readable message for any thrown or rejected value; never throws itself. */
export function failureMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  try {
    return String(err);
  } catch {
    return "unexpected error";
  }
}

type Settled<T extends readonly unknown[]> = { -readonly [P in keyof T]: PromiseSettledResult<Awaited<T[P]>> };

/**
 * `onSettled` gets every outcome, fulfilled or rejected; if it throws,
 * `onFailed` gets the failure's message instead.
 */
export function settleAll<T extends readonly unknown[] | []>(
  work: T,
  onSettled: (results: Settled<T>) => void,
  onFailed: (message: string) => void,
): void {
  Promise.allSettled(work)
    .then((results) => onSettled(results as Settled<T>))
    .catch((err: unknown) => onFailed(failureMessage(err)));
}
