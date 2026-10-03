# Global pause and rule chains

`culture-rules runs pause --apply` sets the global pause flag and
`runs resume --apply` lifts it (admin only, audited). While the engine is
paused, nothing fires.
Events that arrive during the pause and rules that were already waiting are
handled differently, on purpose:

- **A new trigger event that arrives while paused is dropped** (spec h175:
  "after global pause, a matching event fires nothing"). The node evaluates
  it, every rule decides `paused`, and the event is consumed. It does **not**
  fire after resume, and redelivering the same event (same envelope id) does
  not either. Only a new event published after resume fires.
- **A dependant already waiting for its predecessor is kept.** If a rule is
  waiting (`blocked_by_predecessor`, from *must run after* / *may run
  after*) and its predecessor's run settles during the pause, the node
  defers the re-evaluation instead of recording a `paused` skip. Each cycle
  reports the deferral under `deferred` with the reason `paused`. The first
  cycle after resume re-evaluates the dependant once, and it fires or skips
  as it would have without the pause. The deferral is the chain consumer's
  persisted cursor, so restarting a node during the pause loses nothing.

Running steps are not interrupted by a pause. Completions that arrive for
accepted work are still recorded, but no new step is dispatched until resume.
