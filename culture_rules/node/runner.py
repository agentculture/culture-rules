"""What ``culture-rules node run`` needs: open the store, the event source, the ports.

The node talks to the store directly - it *is* the engine, not an API client. This is the
only CLI verb that does; the CLI module imports this package lazily, inside the handler.
Every seam here is a module-level function so tests (and embedders) can replace it.

Production wiring done by :func:`run_node`:

* **Human asks** - :func:`open_emitter` publishes through events-cli when it is installed
  and otherwise records each emitted envelope (e.g. ``human.ask.requested``) into the
  store's ``events`` collection, so ``human`` actors always get a
  :class:`~culture_rules.actors.human.HumanAdapter`.
* **Run reports** - :func:`open_reporter` posts finished-run summaries to
  ``CULTURE_RULES_REPORT_CHANNEL`` (unset: no reporter) through the agent mesh
  (``culture channel message``) when ``culture`` is on PATH, else to the log.
* **Logs** - :func:`~culture_rules.ops.logs.configure_logging` with the node's host.
* **Built-in code steps** - a ``code`` step with no actor runs the built-in named by its
  ``config.builtin`` (:class:`BuiltinCodePort`): ``gate``, the PR fixer's test gate and diff
  guard (:class:`~culture_rules.actors.gate.GatePort`, which runs commands only through
  ``CULTURE_RULES_GATE_RUN_AS`` and refuses while it is unset), ``github.threads`` and
  ``github.threads_addressed`` (d15, the fixer's trusted review threads and the agent's
  replies to them, :mod:`culture_rules.node.actions.github_pr`), ``review`` (d20, the
  reviewer's verdict, :mod:`culture_rules.actors.review`), ``sonar.gate_issues`` (d21, the
  issues behind a PR's failing SonarCloud gate, :mod:`culture_rules.node.actions.sonar`),
  ``gitguardian.findings`` and ``gitguardian.hold`` (d25, a PR head's GitGuardian findings
  and the fix-run hold while GitGuardian fails, :mod:`culture_rules.node.actions.gitguardian`),
  ``queue.add`` and ``queue.progress`` (#35, d29/d30, the fixer's first-come-first-served
  queue, :mod:`culture_rules.node.actions.queue`), ``handoff.export`` and ``handoff.import``
  (#39, the Codex fixer's commits carried from spark to spark2's gate through the store,
  :mod:`culture_rules.actors.handoff`), ``newspaper.sections``, ``newspaper.filter``,
  ``newspaper.render`` and ``newspaper.issue`` (#53, the daily newspaper,
  :mod:`culture_rules.node.actions.newspaper`), ``keyset.filter`` and ``keyset.add`` (named
  sets of keys remembered for a window, :mod:`culture_rules.node.actions.keyset`) and
  ``action`` (d12), which
  never reaches this port: the executor routes a ``builtin: action`` step exactly like a
  rule's terminal action, to the ``action:<kind>`` port through the actor router
  (:mod:`culture_rules.model.action_step`).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.reports import RunReporter
from culture_rules.events.emit import Emitter, engine_app_source
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.events.source import EventFabricError, EventSource
from culture_rules.model.action_step import ACTION_BUILTIN
from culture_rules.node.mesh import MeshPoster
from culture_rules.ops.logs import configure_logging
from culture_rules.ops.nodename import node_name
from culture_rules.store.port import DuplicateKeyError, StoragePort, StoreError

__all__ = [
    "REPORT_CHANNEL_ENV",
    "BuiltinCodePort",
    "LoggingPoster",
    "MeshPoster",
    "NodeSetupError",
    "MissingExtraPort",
    "NoopAction",
    "StoreEventSink",
    "default_host",
    "default_ports",
    "open_emitter",
    "open_event_source",
    "open_reporter",
    "open_store",
    "run_node",
]

REPORT_CHANNEL_ENV = "CULTURE_RULES_REPORT_CHANNEL"
"""Mesh channel finished-run summaries are posted to (unset: no run reports)."""

log = logging.getLogger("culture_rules.node")


class NodeSetupError(RuntimeError):
    """The node cannot start: the store is not configured or not reachable."""


def default_host() -> str:
    """This machine's node name: ``CULTURE_RULES_NODE_NAME``, else the short hostname."""
    return node_name()


def open_store() -> StoragePort:
    """The MongoDB store configured by ``CULTURE_RULES_MONGO_*`` (needs the ``store`` extra).

    Raises :class:`~culture_rules.store.port.StoreError` (``ConfigError``) when unset.
    """
    from culture_rules.store.mongo import MongoConfig, MongoStore  # noqa: PLC0415 - lazy

    return MongoStore(MongoConfig.from_env())


def open_event_source(host: str) -> EventSource | None:
    """This host's durable events-cli subscriptions, or None when they cannot be set up.

    Any setup failure - events-cli missing, a subscription it rejects, the broker
    unreachable - degrades the node to running without ingest instead of crashing it.
    """
    from culture_rules.events.events_cli_adapter import open_host_source  # noqa: PLC0415

    try:
        source = open_host_source(host)
        source.ensure()
    except EventFabricError as exc:
        log.warning("no event source for %s: %s", host, exc)
        return None
    return source


class StoreEventSink:
    """Records emitted envelopes into the store's ``events`` collection (no event bus).

    The stored document has the ingest shape, so triggers, replay and the asks UI read it
    like any ingested event; a re-published envelope id is stored once.
    """

    def __init__(self, store: Any, host: str) -> None:
        self._store = store
        self._host = host

    def publish(self, envelope: Mapping[str, Any]) -> None:
        try:
            self._store.insert(EVENTS_COLLECTION, event_document(envelope, host=self._host))
        except DuplicateKeyError:
            pass


def _events_cli_client() -> Any:
    """An events-cli publish client for the configured broker (needs the ``events`` extra)."""
    from culture_rules.events.events_cli_adapter import open_client  # noqa: PLC0415

    return open_client()


def open_emitter(store: Any, host: str) -> Emitter:
    """The node's emitter: events-cli when available, else recorded into the store."""
    from culture_rules.events.events_cli_adapter import EventsCliSink  # noqa: PLC0415

    source = engine_app_source(host)
    try:
        sink: Any = EventsCliSink(_events_cli_client())
    except Exception as exc:  # noqa: BLE001 - any events-cli/paho setup failure: fall back
        log.warning("no event bus for %s (%s); recording emitted events in the store", host, exc)
        sink = StoreEventSink(store, host)
    return Emitter(sink, source=source)


class LoggingPoster:
    """A :class:`~culture_rules.engine.reports.ChannelPoster` that only logs the report."""

    def post(self, channel: str, text: str) -> None:
        log.info("run report for %s: %s", channel, text)


def open_reporter(env: Mapping[str, str] | None = None) -> RunReporter | None:
    """A run reporter for ``CULTURE_RULES_REPORT_CHANNEL``, or None when it is unset."""
    channel = ((os.environ if env is None else env).get(REPORT_CHANNEL_ENV) or "").strip()
    if not channel:
        return None
    culture = shutil.which("culture")
    poster: Any = MeshPoster(culture) if culture else LoggingPoster()
    return RunReporter(poster, channel=channel)


class NoopAction:
    """The built-in ``noop`` action: completes at once, does nothing."""

    supports_idempotency_key = True

    def invoke(
        self,
        _input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        return InvocationResult.completed({})


class MissingExtraPort:
    """Stands in for an action port whose optional extra is not installed.

    Every invocation fails ``extra_missing`` (non-retryable) so the run says what to
    install rather than failing ``no_actor_port``.
    """

    supports_idempotency_key = False

    def __init__(self, extra: str) -> None:
        self.extra = extra

    def invoke(
        self,
        _input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        return InvocationResult.failed(
            f"extra_missing: install culture-rules[{self.extra}]", retryable=False
        )


class BuiltinCodePort:
    """Routes an actor-less ``code`` step to the built-in its ``config.builtin`` names.

    An unknown or missing name fails the step at once (``no_builtin``): a code step that
    names neither an actor nor a built-in has nothing to run. ``action`` is registered but
    served by the executor's action routing; reaching it here means the step was not
    routed as an action, which fails ``action_step_unrouted`` instead of running anything."""

    supports_idempotency_key = True  # every built-in here is safe to re-invoke

    def __init__(self, builtins: Mapping[str, Any]) -> None:
        self._builtins = dict(builtins)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        name = (context.config or {}).get("builtin")
        if name == ACTION_BUILTIN and name not in self._builtins:
            return InvocationResult.failed(
                "action_step_unrouted: a builtin action step runs through the action router",
                retryable=False,
            )
        port = self._builtins.get(name) if isinstance(name, str) else None
        if port is None:
            known = ", ".join(sorted(self._builtins)) or "none"
            return InvocationResult.failed(
                f"no_builtin: code step names no actor and builtin {name!r} is unknown "
                f"(known: {known})",
                retryable=False,
            )
        return port.invoke(input, idempotency_key, deadline, context=context)


def default_ports(store: StoragePort, host: str) -> dict[str, Any]:
    """Action ports for every catalogued kind (stored actors are wired by the router).

    A port whose extra is missing (the ``github.*`` kinds need ``cryptography``) is replaced
    by one that fails ``extra_missing``. Detection uses ``find_spec``: nothing is imported.
    ``code`` serves actor-less code steps through :class:`BuiltinCodePort`.
    ``action:audio.transcribe`` (:class:`~culture_rules.node.actions.stt.AudioTranscribePort`)
    sends an audio file under the actor's ``audio_root`` to its OpenAI-compatible STT endpoint,
    split into windows with ffmpeg on this host, and writes the transcript under
    ``output_root``; it needs ``ffmpeg``/``ffprobe`` on PATH only when it runs.
    ``action:transcript.render`` (``node.actions.transcript_render.TranscriptRenderPort``)
    renders one transcript under the transcripts root, with its summary, as an EPUB (stdlib).
    ``action:decider.assess`` (``node.actions.decider.DeciderAssessPort``) asks the mesh's
    decider for probabilities over a step's questions (``/v1/systemone``, stdlib).
    ``action:repodocs.sync`` (``node.actions.repodocs.RepodocsSyncPort``) pushes every top-level
    repo's devague record into the library its actor describes (git + stdlib HTTP, #124).
    """
    del host
    from culture_rules.actors.gate import GatePort  # noqa: PLC0415
    from culture_rules.actors.handoff import (  # noqa: PLC0415
        EXPORT_BUILTIN,
        IMPORT_BUILTIN,
        HandoffExportPort,
        HandoffImportPort,
    )
    from culture_rules.actors.review import REVIEW_BUILTIN, ReviewVerdictPort  # noqa: PLC0415
    from culture_rules.node.actions import checklist  # noqa: PLC0415
    from culture_rules.node.actions.attachment_fetch import (  # noqa: PLC0415
        ATTACHMENT_FETCH_BUILTIN,
        AttachmentFetchPort,
    )
    from culture_rules.node.actions.cursor import (  # noqa: PLC0415
        CURSOR_ADVANCE_BUILTIN,
        CursorAdvancePort,
    )
    from culture_rules.node.actions.decider import DeciderAssessPort  # noqa: PLC0415
    from culture_rules.node.actions.discord_react import DiscordReactAction  # noqa: PLC0415
    from culture_rules.node.actions.discord_read import DiscordReadAction  # noqa: PLC0415
    from culture_rules.node.actions.discord_reply import DiscordReplyAction  # noqa: PLC0415
    from culture_rules.node.actions.discord_sweep import DiscordSweepAction  # noqa: PLC0415
    from culture_rules.node.actions.email import EmailSendPort  # noqa: PLC0415
    from culture_rules.node.actions.fixer_cooldown import (  # noqa: PLC0415
        FIXER_COOLDOWN_BUILTIN,
        FixerCooldownPort,
    )
    from culture_rules.node.actions.gitguardian import (  # noqa: PLC0415
        FINDINGS_BUILTIN,
        HOLD_BUILTIN,
        GitGuardianPort,
    )
    from culture_rules.node.actions.github import (  # noqa: PLC0415
        GitHubCommentPort,
        GitHubPrHeadPort,
    )
    from culture_rules.node.actions.github_merge import GitHubMergePort  # noqa: PLC0415
    from culture_rules.node.actions.github_pr import (  # noqa: PLC0415
        ADDRESSED_BUILTIN,
        THREADS_BUILTIN,
        AddressedThreadsPort,
        GitHubPushPort,
        GitHubReviewReplyPort,
        GitHubThreadsPort,
    )
    from culture_rules.node.actions.http import HttpCallPort  # noqa: PLC0415
    from culture_rules.node.actions.jira import JiraCommentPort  # noqa: PLC0415
    from culture_rules.node.actions.json_read import (  # noqa: PLC0415
        JSON_READ_BUILTIN,
        JsonReadPort,
    )
    from culture_rules.node.actions.keyset import (  # noqa: PLC0415
        KEYSET_ADD_BUILTIN,
        KEYSET_FILTER_BUILTIN,
        KeysetAddPort,
        KeysetFilterPort,
    )
    from culture_rules.node.actions.library import LibraryPushPort  # noqa: PLC0415
    from culture_rules.node.actions.llm import LlmCompletePort  # noqa: PLC0415
    from culture_rules.node.actions.machine import MachineCommandPort  # noqa: PLC0415
    from culture_rules.node.actions.markdown_html import (  # noqa: PLC0415
        FROM_HTML_BUILTIN,
        MarkdownFromHtmlPort,
    )
    from culture_rules.node.actions.markdown_render import (  # noqa: PLC0415
        MARKDOWN_RENDER_BUILTIN,
        MarkdownRenderPort,
    )
    from culture_rules.node.actions.message import (  # noqa: PLC0415
        DiscordMessageAction,
        MessageAction,
    )
    from culture_rules.node.actions.newspaper import (  # noqa: PLC0415
        FILTER_BUILTIN,
        ISSUE_BUILTIN,
        RENDER_BUILTIN,
        SECTIONS_BUILTIN,
        NewspaperFilterPort,
        NewspaperIssuePort,
        NewspaperRenderPort,
        NewspaperSectionsPort,
    )
    from culture_rules.node.actions.queue import (  # noqa: PLC0415
        QUEUE_ADD_BUILTIN,
        QUEUE_PROGRESS_BUILTIN,
        QUEUE_STOP_BUILTIN,
        QueueAddPort,
        QueueProgressPort,
        QueueStopPort,
    )
    from culture_rules.node.actions.repodocs import RepodocsSyncPort  # noqa: PLC0415
    from culture_rules.node.actions.run_read import RUN_READ_BUILTIN, RunReadPort  # noqa: PLC0415
    from culture_rules.node.actions.search import SearchWebPort  # noqa: PLC0415
    from culture_rules.node.actions.sections import (  # noqa: PLC0415
        SECTIONS_BUILTIN as DOCUMENT_SECTIONS_BUILTIN,
    )
    from culture_rules.node.actions.sections import DocumentSectionsPort  # noqa: PLC0415
    from culture_rules.node.actions.select import SELECT_BUILTIN, SelectPort  # noqa: PLC0415
    from culture_rules.node.actions.sonar import SONAR_BUILTIN, SonarGateIssuesPort  # noqa: PLC0415
    from culture_rules.node.actions.stt import AudioTranscribePort  # noqa: PLC0415
    from culture_rules.node.actions.transcript_render import (  # noqa: PLC0415
        TranscriptRenderPort,
    )

    message = MessageAction(store)
    has_github = importlib.util.find_spec("cryptography") is not None
    github: Any = GitHubCommentPort(store) if has_github else MissingExtraPort("github")
    push: Any = GitHubPushPort(store) if has_github else MissingExtraPort("github")
    merge: Any = GitHubMergePort(store) if has_github else MissingExtraPort("github")
    reply: Any = GitHubReviewReplyPort(store) if has_github else MissingExtraPort("github")
    head: Any = GitHubPrHeadPort(store) if has_github else MissingExtraPort("github")
    threads: Any = GitHubThreadsPort(store) if has_github else MissingExtraPort("github")
    findings: Any = GitGuardianPort(store) if has_github else MissingExtraPort("github")
    hold: Any = GitGuardianPort(store, hold=True) if has_github else MissingExtraPort("github")
    return {
        "action:noop": NoopAction(),
        "action:github.pr_head": head,  # not a rule action: the wait guard's head lookup
        "action:message": message,
        "action:mesh.message": message,  # legacy alias of message
        "action:discord.message": DiscordMessageAction(store),
        "action:discord.read": DiscordReadAction(store),  # one message in full + attachments
        "action:discord.react": DiscordReactAction(store),  # the bot's reaction on a message
        "action:discord.reply": DiscordReplyAction(store),  # a command's private answer
        "action:discord.sweep": DiscordSweepAction(store),  # a channel since its cursor
        "action:github.comment": github,
        "action:github.push": push,
        "action:github.merge": merge,  # the one merge: a click in a merge-trusted workflow
        "action:github.review_reply": reply,
        "action:jira.comment": JiraCommentPort(store),
        "action:email.send": EmailSendPort(store),  # #53: Resend
        "action:search.web": SearchWebPort(store),  # #53: Brave
        "action:llm.complete": LlmCompletePort(store),  # #53: OpenAI-compatible, no tools
        "action:library.push": LibraryPushPort(
            store
        ),  # #53, #86: upload to the actor's library (+ queue)
        "action:audio.transcribe": AudioTranscribePort(store),  # file-in STT, split by ffmpeg
        "action:transcript.render": TranscriptRenderPort(store),  # one transcript as an EPUB
        "action:decider.assess": DeciderAssessPort(store),  # the mesh decider: probabilities
        "action:repodocs.sync": RepodocsSyncPort(store),  # #124: repos' devague record -> library
        "action:http.call": HttpCallPort(store),
        "action:machine.command": MachineCommandPort(store),
        "code": BuiltinCodePort(
            {
                "gate": GatePort.from_env(store, pr_lookup=head),
                REVIEW_BUILTIN: ReviewVerdictPort(store),
                THREADS_BUILTIN: threads,
                ADDRESSED_BUILTIN: AddressedThreadsPort(),
                SONAR_BUILTIN: SonarGateIssuesPort(),  # d21: the PR's failing gate's issues
                FINDINGS_BUILTIN: findings,  # d25: GitGuardian's findings, for the comment
                HOLD_BUILTIN: hold,  # d25: no fix run while GitGuardian fails on the head
                QUEUE_ADD_BUILTIN: QueueAddPort(store),  # #35 d29: the fixer queue
                QUEUE_PROGRESS_BUILTIN: QueueProgressPort(store, pr_lookup=head),
                QUEUE_STOP_BUILTIN: QueueStopPort(store, pr_lookup=head),  # d34: /stop, 👎
                SECTIONS_BUILTIN: NewspaperSectionsPort(),  # #53: the daily newspaper
                FILTER_BUILTIN: NewspaperFilterPort(),
                RENDER_BUILTIN: NewspaperRenderPort(),
                ISSUE_BUILTIN: NewspaperIssuePort(store),  # one issue per local date
                EXPORT_BUILTIN: HandoffExportPort.from_env(store),  # #39: Codex commits out
                IMPORT_BUILTIN: HandoffImportPort.from_env(store),  # #39: ... into spark2
                FIXER_COOLDOWN_BUILTIN: FixerCooldownPort(store),  # #39: capacity_exhausted
                SELECT_BUILTIN: SelectPort(),  # #39: the gate's one source, whichever path ran
                JSON_READ_BUILTIN: JsonReadPort(),  # named fields from a JSON file under a root
                MARKDOWN_RENDER_BUILTIN: MarkdownRenderPort(),  # fields as one Markdown page
                FROM_HTML_BUILTIN: MarkdownFromHtmlPort(),  # HTML as Markdown, in chunks
                DOCUMENT_SECTIONS_BUILTIN: DocumentSectionsPort(),  # a document split at a heading
                RUN_READ_BUILTIN: RunReadPort(store),  # a step's outputs from an earlier run
                ATTACHMENT_FETCH_BUILTIN: AttachmentFetchPort(),  # pdf/epub/md to files (d7, d10)
                CURSOR_ADVANCE_BUILTIN: CursorAdvancePort(store),  # compare-and-set a cursor
                KEYSET_FILTER_BUILTIN: KeysetFilterPort(store),  # drop items whose key was seen
                KEYSET_ADD_BUILTIN: KeysetAddPort(store),  # remember items' keys for a window
                checklist.MERGE_BUILTIN: checklist.ChecklistMergePort(
                    store
                ),  # stored lists, stable ids
                checklist.SET_BUILTIN: checklist.ChecklistSetPort(store),  # a newer flip wins
                checklist.RENDER_BUILTIN: checklist.ChecklistRenderPort(
                    store
                ),  # Markdown + revision
                checklist.BIND_BUILTIN: checklist.ChecklistBindPort(
                    store
                ),  # pushed item + revision
            }
        ),
    }


def run_node(host: str | None = None, *, once: bool = False, idle: float = 1.0) -> dict[str, Any]:
    """Open everything, run the node (one cycle with ``once``) and summarise what it did."""
    from culture_rules.node.actors import default_factories  # noqa: PLC0415
    from culture_rules.node.daemon import Node, NodeOptions  # noqa: PLC0415

    host = host or default_host()
    try:
        store = open_store()
    except StoreError as exc:
        raise NodeSetupError(f"cannot open the store: {exc}") from exc
    configure_logging(host=host)
    source = open_event_source(host)
    node = Node(
        store,
        host,
        actors=default_ports(store, host),
        adapters=default_factories(store, emitter=open_emitter(store, host)),
        event_source=source,
        reporter=open_reporter(),
        # one cycle never opens a long-lived gateway connection
        options=NodeOptions(listen_gateways=not once),
    )
    if once:
        report = node.run_once()
        return {
            "host": host,
            "cycles": 1,
            "events": source is not None,
            "report": report.to_dict(),
        }
    import signal  # noqa: PLC0415

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: node.stop())
    cycles = node.run(idle=idle)
    return {
        "host": host,
        "cycles": cycles,
        "events": source is not None,
        "errors": [f"{type(e).__name__}: {e}" for e in node.errors[-20:]],
    }
