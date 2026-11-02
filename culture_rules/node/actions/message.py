"""The ``message`` action: post to a Discord channel or to a Culture mesh channel.

``params.actor`` set: the actor is an app actor with surface ``discord`` and the message goes
to Discord channel ``channel`` (restricted to ``connection.channels`` when that is set), with
the bot token from ``connection.bot_token`` (a ``grant:NAME`` reference resolved at call
time). No actor: the message goes to the mesh with ``culture channel message`` (argv list,
no shell) through :class:`~culture_rules.node.mesh.MeshPoster`.

Mass mentions are always suppressed on Discord (``allowed_mentions.parse`` is empty). The
token and the message text are never logged or echoed in errors.

``buttons`` put one row of buttons under a Discord message. A button with a ``value`` (a JSON
object) is posted as ``<custom_id>:<token>`` and its value is kept in ``discord_buttons``
before the post (:mod:`culture_rules.apps.discord_buttons`), so the click's event carries it as
``data.value``; only this port writes it, a clicker cannot. Each stored button also records
who posted it, ``posted_by: {run_id, workflow_id}`` (the invoking run and its workflow id,
read from ``runs``; ``workflow_id`` is ``None`` when the run cannot be read), which the click
event carries as ``data.posted_by`` (deviation d2). A post Discord definitively refused
(a 4xx but 429, :attr:`~culture_rules.apps.discord_rest.DiscordError.refused`) deletes the
kept values; after a timeout, a 5xx, a 429 or a lost connection the message may be up, so they
stay (the TTL index deletes them). With buttons the output is
``{"message_id", "custom_ids"}`` (the posted ids, in button order).

``thread_name`` makes the post a forum post: ``channel`` must be a forum (type 15, else
``bad_params`` before any post), the thread is named ``thread_name`` (cut to 100 characters at
the last space) and tagged with the forum tags whose names match ``tags`` exactly (at most 5;
names the forum lacks come back in ``tags_missing``). A text longer than one message is split at
line breaks (a longer single line hard) into pieces that join back to the text: the first
starts the thread and the rest are replies in it, in order (a piece that is only blank space
is not posted, since Discord refuses a blank message). A forum whose flags require a tag
(``REQUIRE_TAG``) fails ``bad_params`` before any post when none of ``tags`` matched. Starting
the thread is retryable only when Discord surely did not create it (a 429, a refused
connection); a timeout, a 5xx or a lost connection is an unknown outcome, not retried. A reply
Discord rate-limits (a 429: not posted) is sent again after its ``retry_after`` (at most 10 s a
wait, at most 3 waits a step, and never a wait that would reach the step's deadline).
Once the thread exists any other failure is not retryable (a retry would start a second
thread) and its error names the thread and the replies posted. The output is
``{"message_id", "thread_id", "reply_ids", "tags_missing"}``.
``tags`` without ``thread_name`` and ``buttons`` with it are ``bad_params``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404 - only for TimeoutExpired; the CLI runs in node.mesh
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.actors import secrets
from culture_rules.apps import discord_buttons as buttons_mod
from culture_rules.apps.discord_rest import MAX_CONTENT, DiscordClient, DiscordError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.mesh import MeshPoster

__all__ = ["ACTORS_COLLECTION", "DiscordMessageAction", "MessageAction"]

ACTORS_COLLECTION = "actors"
log = logging.getLogger("culture_rules.node")


def _fail(error: str) -> InvocationResult:
    return InvocationResult.failed(error, retryable=False)


#: Discord's channel type of a forum, the most tags a forum post takes and its longest title
FORUM_CHANNEL_TYPE = 15
MAX_FORUM_TAGS = 5
MAX_THREAD_NAME = 100
#: a rate-limited forum reply (a 429, which Discord did not act on) is sent again after its
#: ``retry_after``, waiting at most this long each time and at most this many times a step
MAX_REPLY_WAIT_S = 10.0
MAX_REPLY_WAITS = 3
DEFAULT_REPLY_WAIT_S = 1.0
#: Discord's channel flag of a forum on which every post must carry a tag
REQUIRE_TAG = 1 << 4


def _flags(channel: Mapping[str, Any]) -> int:
    flags = channel.get("flags")
    return flags if isinstance(flags, int) and not isinstance(flags, bool) else 0


class _ReplyWaits:
    """The rate-limit waits one forum step has left: at most :data:`MAX_REPLY_WAITS`, and
    none that would end at or past the step's ``deadline`` (when there is one)."""

    def __init__(self, deadline: datetime | None, clock: Callable[[], datetime]) -> None:
        self.left = MAX_REPLY_WAITS
        self._deadline = deadline
        self._clock = clock

    def take(self, seconds: float) -> str | None:
        """Use one wait of ``seconds``: ``None`` when it may be slept, else why not."""
        if self.left <= 0:
            return f"no rate-limit waits left (at most {MAX_REPLY_WAITS} a step)"
        if self._deadline is not None and (
            self._clock() + timedelta(seconds=seconds) >= self._deadline
        ):
            return "waiting would pass the step deadline"
        self.left -= 1
        return None


def _forum_params(input: Mapping[str, Any]) -> tuple[str, list[str]] | None:  # noqa: A002
    """``(thread_name, tags)`` of a forum post, ``None`` without ``thread_name``; raises
    ``ValueError`` on params that cannot be posted."""
    name, tags = input.get("thread_name"), input.get("tags")
    if name is None:
        if tags not in (None, []):
            raise ValueError("tags need a thread_name")
        return None
    if not isinstance(name, str) or not name.strip():
        raise ValueError("thread_name must be a non-empty string")
    if input.get("buttons"):
        raise ValueError("buttons cannot go on a forum post")
    if tags is None:
        tags = []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("tags must be a list of tag names")
    if len(tags) > MAX_FORUM_TAGS:
        raise ValueError(f"a forum post takes at most {MAX_FORUM_TAGS} tags")
    return name, tags


def thread_title(name: str, limit: int = MAX_THREAD_NAME) -> str:
    """``name`` cut to ``limit`` characters at its last space (a hard cut when it has none)."""
    if len(name) <= limit:
        return name
    space = name[: limit + 1].rfind(" ")
    cut = name[:space].rstrip() if space > 0 else ""
    return cut or name[:limit]


def split_text(text: str, limit: int = MAX_CONTENT) -> list[str]:
    """``text`` in pieces of at most ``limit`` characters, each ending at a line break where
    one falls in reach (a longer single line is cut hard); ``"".join`` gives ``text`` back."""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        end = window.rfind("\n") + 1
        if end <= 0 or not window[:end].strip():
            end = limit
        pieces.append(rest[:end])
        rest = rest[end:]
    pieces.append(rest)
    return pieces


class MessageAction:
    """Action port for ``message`` (and its legacy alias ``mesh.message``)."""

    supports_idempotency_key = False  # neither Discord nor the mesh deduplicates

    def __init__(
        self,
        store: Any,
        *,
        resolve_secret: Callable[[str], str] | None = None,
        transport: Transport | None = None,
        discord_base_url: str | None = None,
        mesh_executable: str | None = None,
        mesh_run: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._indexed = False
        self._resolve = resolve_secret or secrets.resolve_or_literal
        self._transport = transport
        self._base_url = discord_base_url
        self._mesh_executable = mesh_executable
        self._mesh_run = mesh_run

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del idempotency_key
        channel, text = input.get("channel"), input.get("text")
        if not isinstance(channel, str) or not channel or not isinstance(text, str) or not text:
            return _fail("message needs a non-empty channel and text")
        if context.actor:
            try:
                forum = _forum_params(input)
                components, valued = buttons_mod.prepare_buttons(input.get("buttons"))
            except ValueError as exc:
                return _fail(f"bad_params: {exc}")
            if forum is not None:
                return self._forum(context.actor, channel, text, *forum, deadline=deadline)
            poster = self._posted_by(context.run_id, context.step_id) if valued else None
            return self._discord(context.actor, channel, text, components, valued, poster)
        return self._mesh(channel, text)

    def _posted_by(self, run_id: str, step: str) -> dict[str, Any]:
        """``{run_id, workflow_id, step}`` of the run and step posting valued buttons; the
        workflow id is read from the run (``None`` when it cannot be read: github.merge then
        refuses the button). ``step`` is the step's key in that run, so github.merge can tell
        a workflow step (covered by the workflow's digest) from the rule's own action."""
        workflow_id = None
        try:
            run = self._store.get(RUNS_COLLECTION, run_id)
        except Exception as exc:  # noqa: BLE001 - the post goes ahead without the workflow id
            log.warning("reading the posting run failed (%s)", type(exc).__name__)
            run = None
        if isinstance(run, Mapping) and isinstance(run.get("workflow_id"), str):
            workflow_id = run["workflow_id"]
        return {"run_id": run_id, "workflow_id": workflow_id, "step": step}

    def _mesh(self, channel: str, text: str) -> InvocationResult:
        executable = self._mesh_executable or shutil.which("culture")
        if not executable:
            return InvocationResult.failed("the culture CLI is not on PATH", retryable=True)
        try:
            MeshPoster(executable, run=self._mesh_run).post(channel, text)
        except subprocess.TimeoutExpired:
            # the message may have been sent: a retry could duplicate it
            log.warning("mesh message to %s timed out (outcome unknown)", channel)
            return InvocationResult.failed(
                "mesh message timed out (outcome unknown)", retryable=False
            )
        except Exception as exc:  # noqa: BLE001 - any failure to run the CLI is retryable
            log.warning("mesh message to %s failed (%s)", channel, type(exc).__name__)
            return InvocationResult.failed(
                f"mesh message failed ({type(exc).__name__})", retryable=True
            )
        log.info("mesh message posted to %s", channel)
        return InvocationResult.completed({})

    def _discord(
        self,
        actor_id: str,
        channel: str,
        text: str,
        components: list[dict[str, Any]],
        valued: list[tuple[str, Any, bool]],
        posted_by: Mapping[str, Any] | None = None,
    ) -> InvocationResult:
        bot = self._discord_token(actor_id, channel)
        if isinstance(bot, InvocationResult):
            return bot
        kept: list[str] = []
        if valued:
            try:
                kept = self._keep_values(actor_id, channel, valued, posted_by)
            except Exception as exc:  # noqa: BLE001 - nothing was posted: retry later
                log.warning("keeping button values failed (%s)", type(exc).__name__)
                return InvocationResult.failed(
                    f"keeping button values failed ({type(exc).__name__})", retryable=True
                )
        try:
            out = self._client(bot).post_message(channel, text, components=components)
        except DiscordError as exc:
            # only a definite refusal drops the values: after a timeout, a 5xx, a 429 or a
            # lost connection the message may be up, and its buttons keep their meaning
            # (the TTL index deletes values whose post never landed)
            if exc.refused:
                buttons_mod.forget_buttons(self._store, kept)
            log.warning("discord message to %s failed: %s", channel, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        if kept:
            try:
                buttons_mod.set_message_id(self._store, kept, out["message_id"])
            except Exception as exc:  # noqa: BLE001 - the values stand without it
                log.warning("recording the buttons' message id failed (%s)", type(exc).__name__)
        log.info("discord message posted to %s", channel)
        if components:
            out = {**out, "custom_ids": [b["custom_id"] for b in components[0]["components"]]}
        return InvocationResult.completed(out)

    def _client(self, bot: str) -> DiscordClient:
        kwargs: dict[str, Any] = {"transport": self._transport}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return DiscordClient(bot, **kwargs)

    def _forum(
        self,
        actor_id: str,
        channel: str,
        text: str,
        name: str,
        tags: list[str],
        *,
        deadline: datetime | None = None,
    ) -> InvocationResult:
        """Start a post in the forum ``channel`` and continue a long ``text`` as replies."""
        bot = self._discord_token(actor_id, channel)
        if isinstance(bot, InvocationResult):
            return bot
        client = self._client(bot)
        try:
            forum = client.get_channel(channel)
        except DiscordError as exc:
            log.warning("reading discord channel %s failed: %s", channel, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        if forum.get("type") != FORUM_CHANNEL_TYPE:
            return _fail(f"bad_params: channel {channel!r} is not a forum channel")
        by_name: dict[str, str] = {}
        for tag in forum.get("available_tags") or []:
            by_name.setdefault(tag["name"], tag["id"])
        tag_ids = list(dict.fromkeys(by_name[t] for t in tags if t in by_name))
        missing = [t for t in tags if t not in by_name]
        if _flags(forum) & REQUIRE_TAG and not tag_ids:
            return _fail(
                f"bad_params: forum {channel!r} requires a tag and none of tags matched"
                f" (tags_missing: {missing})"
            )
        first, *rest = split_text(text)
        try:
            started = client.create_forum_post(channel, thread_title(name), first, tag_ids)
        except DiscordError as exc:
            log.warning("discord forum post to %s failed: %s", channel, exc)
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        thread_id = started["thread_id"]
        reply_ids: list[str] = []
        waits = _ReplyWaits(deadline, self._clock)
        for piece in rest:
            if not piece.strip():  # Discord refuses a blank message; nothing visible is lost
                continue
            try:
                reply_ids.append(self._reply(client, thread_id, piece, waits)["message_id"])
            except Exception as exc:  # noqa: BLE001 - the thread exists: never start another
                detail = str(exc) if isinstance(exc, DiscordError) else type(exc).__name__
                log.warning("reply in discord thread %s failed: %s", thread_id, detail)
                return _fail(
                    f"forum post thread {thread_id} started but a reply failed ({detail});"
                    f" starter {started['message_id']}, replies posted: {reply_ids}"
                )
        log.info("discord forum post started in %s (%d replies)", channel, len(reply_ids))
        return InvocationResult.completed(
            {
                "message_id": started["message_id"],
                "thread_id": thread_id,
                "reply_ids": reply_ids,
                "tags_missing": missing,
            }
        )

    def _reply(
        self, client: DiscordClient, thread_id: str, piece: str, waits: _ReplyWaits
    ) -> dict[str, str]:
        """Post one reply in ``thread_id``; a 429 (Discord did not post it) waits its
        ``retry_after`` (at most :data:`MAX_REPLY_WAIT_S`) and sends the same reply again,
        while ``waits`` allows it. Any other failure, or a wait it refuses, raises."""
        while True:
            try:
                return client.post_message(thread_id, piece)
            except DiscordError as exc:
                if not exc.rate_limited:
                    raise
                wait = DEFAULT_REPLY_WAIT_S if exc.retry_after is None else exc.retry_after
                wait = min(max(wait, 0.0), MAX_REPLY_WAIT_S)
                refusal = waits.take(wait)
                if refusal:
                    raise DiscordError(f"{exc}; {refusal}", retryable=False) from exc
                self._sleep(wait)

    def _discord_token(self, actor_id: str, channel: str) -> str | InvocationResult:
        """The bot token of the Discord app actor ``actor_id`` when it may post to ``channel``,
        else the failed result."""
        actor = self._store.get(ACTORS_COLLECTION, actor_id)
        if actor is None:
            return _fail(f"actor {actor_id!r} not found")
        params = actor.get("params") if isinstance(actor.get("params"), Mapping) else {}
        surface = actor.get("surface") or params.get("surface")
        if surface != "discord":
            return _fail(f"actor {actor_id!r} is not a discord app actor")
        connection = actor.get("connection") or params.get("connection") or {}
        allowed = connection.get("channels")
        if isinstance(allowed, str):  # one channel, not a set of characters
            allowed = [allowed]
        if allowed and channel not in {str(c) for c in allowed}:
            return _fail(f"channel {channel!r} is not in actor {actor_id!r} allow-list")
        ref = connection.get("bot_token")
        if not isinstance(ref, str) or not ref:
            return _fail(f"actor {actor_id!r} has no bot_token reference")
        try:
            return self._resolve(ref)
        except secrets.SecretError as exc:
            return InvocationResult.failed(str(exc), retryable=True)

    def _keep_values(
        self,
        actor_id: str,
        channel: str,
        valued: list[tuple[str, Any, bool]],
        posted_by: Mapping[str, Any] | None,
    ) -> list[str]:
        """Store the valued buttons' meaning before the post (the TTL index first, once)."""
        if not self._indexed:
            try:
                buttons_mod.ensure_buttons_ttl(self._store)
                self._indexed = True
            except Exception as exc:  # noqa: BLE001 - a missing index must not stop the post
                log.warning("ensuring the buttons TTL index failed (%s)", type(exc).__name__)
        return buttons_mod.remember_buttons(
            self._store, actor_id, channel, valued, now=self._clock(), posted_by=posted_by
        )


class DiscordMessageAction(MessageAction):
    """``discord.message``: post through the Discord app actor in ``params.actor``; never the
    mesh. ``params.guild`` is the server the channel was picked from (editor metadata)."""

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if not context.actor:
            return _fail("discord.message needs a Discord app actor in params.actor")
        return super().invoke(input, idempotency_key, deadline, context=context)
