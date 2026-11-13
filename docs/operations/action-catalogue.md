# The action catalogue

The action kinds a rule or step can end in are the extension registry's
(`culture_rules/extensions`). The API serves them, and the editor's action picker is built from
what it serves, so an extension that registers a kind needs no editor change.

## The route

`GET /actions/kinds` (viewer, like other reads; `operation_id` `list_action_kinds`) answers
`{items: [...]}`, one item per registered kind, in catalogue (registration) order. It is built
from `registry()` on every call, never from a list.

Each item has:

- `kind`, `label` (a short name for pickers; the summary when none), `summary`;
- `lenient` (required params are not enforced, as for the legacy `message` placeholder) and
  `aliases` (legacy names, e.g. `mesh.message`);
- `params`, in the order a picker shows them: `name`, `type` (`str`, `int`, `bool`, `dict`,
  `list`, `any`), `required`, and the editor hints below;
- `any_of`: groups of params of which at least one must be set;
- `actors`: which actors can perform it, as alternatives (OR), each a clause of conditions
  (AND) over an enabled actor: `kinds`, `not_kinds`, `surface`, `declares_action` (the actor
  lists the kind in `params.actions`), `has` (these dotted actor params are non-empty) and
  `has_any`. A kind that names none means an app actor that declares it;
- `actorless`: offered even when no enabled actor can perform it;
- `declarable_by_apps`: an `app` actor may list the kind in `params.actions`; the Actors tab
  offers only these (and any kind an actor already lists, so it stays removable). Each
  extension sets it in its `KINDS` (`kind(..., declarable_by_apps=True)`); the default is
  false;
- `shown_as`: `{when_param, kind}` or null. A stored action of this kind that sets
  `when_param` is shown, and saved, as `kind`: core's `message` carries
  `{"when_param": "actor", "kind": "discord.message"}`, the form a Discord message had before
  `discord.message` existed.

The param hints are data on `ParamSpec` (`culture_rules/model/action_kinds.py`), so a new kind
describes its own form:

| Field | Meaning |
|-------|---------|
| `label` | how a picker names the param (default: the name in words) |
| `widget` | `multiline`, `json`, `number`, `buttons`, `command`, `command_args`, `discord_guild`, `discord_channel`, `hidden`; empty: by `type` |
| `choices` | a closed set of `{value, label}` |
| `options_from` | the chosen actor's params key that lists (or, as a mapping, names) the values |
| `choose` | the prompt of the empty choice |
| `needs_actor_param` | shown only when the chosen actor has that param set |
| `advanced` | rarely set: the editor keeps it behind a "More settings" fold, open when the step already sets it |
| `needed` | not required of a stored step (the port refuses it at run time) but the editor asks for it |
| `args_for` | on a `command_args` param: the sibling `command` param whose chosen command's declared arguments it holds (`machine.command`'s `args` has `args_for: "command"`) |

## The surfaces

- CLI: `culture-rules actions kinds [KIND] [--json]` (a name or a legacy alias); `actions
  overview` describes the noun. Read-only, viewer role. A server older than 0.44.0 lacks the
  route: exit 2.
- MCP: the tools `actions_overview` and `actions_kinds`, kept identical to the CLI by the
  parity test.
- Contract: the route and its schemas (`ActionKindList`, `ActionKindItem`, `ActionParam`,
  `ActionShownAs`) are pinned in `api/openapi.json`.

## The editor

`web/src/api/actions.ts` fetches the catalogue once (`useActionCatalogue`), with a loading and
a failed state (Try again). `web/src/rules/ActionPicker.tsx` offers the kinds whose actors
match the clauses, builds each form from the params and widgets, and keeps a stored kind it
does not know as it is. Nothing in the editor is keyed by kind or param name: a legacy form is
shown through `shown_as`, a blank action is the first kind in catalogue order (with no
`params` when the kind takes none), and the command widgets find each other through
`args_for`. The Actors tab's "Actions it can perform" lists the `declarable_by_apps` kinds.

The web tests use a snapshot of the built-in catalogue,
`web/src/fixtures/action-kinds.data.ts`, generated from the Python by
`python3 scripts/export-action-kinds.py --write`; a test fails when it is stale.

## Runner command argument types

A runner actor's registered commands (`machine.command`'s `command` and `args`) declare each
argument's type in the command's `params`. The runner checks the value against the type
before any process starts; a mismatch is a bind error. The types are:

| Type | Accepts |
|------|---------|
| `string` | any string |
| `int` | an integer (not a boolean) |
| `number` | an integer or float (not a boolean) |
| `bool` | a boolean, passed as `true` or `false` |
| `segment` | one safe path segment: 1 to 128 characters, not `.` or `..`, with no `/` or `\`, none of `, ( ) * ? [ ] { }` (a segment often names a directory that becomes a working path), no control character (below 0x20, or 0x7f, NUL included) and no leading `-` (so it never reads as an option); `SCRUM-12` passes, `../x`, `a,b`, `*` and `--help` do not |
