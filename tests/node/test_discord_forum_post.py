"""discord.message with ``thread_name``: a post in a Discord forum channel, tagged by name, a
long text continued as replies in the new thread."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from culture_rules.engine.actorport import FAILED, InvocationContext
from culture_rules.extensions.discord.message import DiscordMessageAction
from culture_rules.extensions.discord.rest import MAX_CONTENT
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
FORUM, TEXT_CHANNEL, THREAD = "77", "78", "900"
TAGS = [{"id": "5001", "name": "Jetson news"}, {"id": "5002", "name": "Other"}]


def ctx():
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor="disc")


class ForumTransport:
    """Routes GETs of a channel, the thread POST and reply POSTs; records every request."""

    def __init__(
        self,
        *,
        thread_status=201,
        reply_statuses=(),
        thread_timeout=False,
        retry_after=0.5,
        thread_exc=None,
        flags=0,
    ):
        self.calls = []
        self.thread_exc = thread_exc
        self.flags = flags
        self.retry_after = retry_after
        self.thread_status = thread_status
        self.reply_statuses = list(reply_statuses)
        self.thread_timeout = thread_timeout
        self.replies = 0

    def __call__(self, url, data, headers, timeout, **kw):
        body = json.loads(data) if data else None
        self.calls.append((url, body))
        if data is None:  # GET /channels/{id}
            channel = url.rsplit("/", 1)[1]
            kind = 15 if channel == FORUM else 0
            body = {"id": channel, "type": kind, "flags": self.flags, "available_tags": TAGS}
            return 200, body, {}
        if url.endswith("/threads"):
            if self.thread_timeout:
                raise TimeoutError("timed out")
            if self.thread_exc is not None:
                raise self.thread_exc
            if self.thread_status == 429:
                return 429, {"message": "rate limited", "retry_after": self.retry_after}, {}
            return self.thread_status, {"id": THREAD, "message": {"id": "m-start"}}, {}
        self.replies += 1
        status = self.reply_statuses.pop(0) if self.reply_statuses else 200
        if status == 429:
            return status, {"message": "rate limited", "retry_after": self.retry_after}, {}
        return status, {"id": f"m-{self.replies}"}, {}

    def posts(self):
        return [(url, body) for url, body in self.calls if body is not None]

    def thread_body(self):
        return next(body for url, body in self.calls if url.endswith("/threads"))


def port(transport, secrets_seen=None, sleep=None, clock=None):
    s = MemoryStore()
    s.put(
        "actors",
        {
            "id": "disc",
            "name": "Disc",
            "kind": "service",
            "surface": "discord",
            "connection": {"bot_token": "grant:DISCORD_BOT"},
        },
    )

    def resolve(ref):
        if secrets_seen is not None:
            secrets_seen.append(ref)
        return "tok-xyz"

    return DiscordMessageAction(
        s,
        resolve_secret=resolve,
        transport=transport,
        sleep=sleep or (lambda _s: None),
        clock=clock or (lambda: NOW),
    )


def post(transport, secrets_seen=None, sleep=None, deadline=LATER, **params):
    params = {"channel": FORUM, "text": "hello forum", **params}
    return port(transport, secrets_seen, sleep).invoke(params, "k", deadline, context=ctx())


# --- the catalogue -------------------------------------------------------------------------


def test_discord_message_catalogues_thread_name_as_a_string():
    assert ACTION_KINDS["discord.message"].params["thread_name"].type == "str"


def test_discord_message_catalogues_tags_as_a_list():
    assert ACTION_KINDS["discord.message"].params["tags"].type == "list"


# --- validation before any secret or request -----------------------------------------------


def test_thread_name_on_a_text_channel_fails_bad_params_without_a_post():
    t = ForumTransport()
    res = post(t, channel=TEXT_CHANNEL, thread_name="A post")
    assert res.outcome == FAILED
    assert res.error.startswith("bad_params")
    assert not res.retryable
    assert t.posts() == []


def test_six_tags_fail_bad_params_without_any_request():
    t, seen = ForumTransport(), []
    res = post(t, seen, thread_name="A post", tags=["a", "b", "c", "d", "e", "f"])
    assert res.error.startswith("bad_params")
    assert t.calls == []
    assert seen == []


def test_five_tags_are_accepted():
    t = ForumTransport()
    res = post(t, thread_name="A post", tags=["a", "b", "c", "d", "e"])
    assert res.outcome == "completed"


def test_tags_without_thread_name_fail_bad_params():
    t, seen = ForumTransport(), []
    res = post(t, seen, tags=["Jetson news"])
    assert res.error.startswith("bad_params")
    assert t.calls == []
    assert seen == []


def test_a_non_list_tags_fails_bad_params():
    t = ForumTransport()
    res = post(t, thread_name="A post", tags="Jetson news")
    assert res.error.startswith("bad_params")
    assert t.calls == []


def test_a_non_string_tag_fails_bad_params():
    t = ForumTransport()
    res = post(t, thread_name="A post", tags=["Jetson news", 3])
    assert res.error.startswith("bad_params")
    assert t.calls == []


def test_an_empty_thread_name_fails_bad_params():
    t = ForumTransport()
    res = post(t, thread_name="")
    assert res.error.startswith("bad_params")
    assert t.calls == []


def test_a_non_string_thread_name_fails_bad_params():
    t = ForumTransport()
    res = post(t, thread_name=12)
    assert res.error.startswith("bad_params")
    assert t.calls == []


def test_buttons_with_thread_name_fail_bad_params():
    t, seen = ForumTransport(), []
    res = post(t, seen, thread_name="A post", buttons=[{"label": "Go", "custom_id": "go"}])
    assert res.error.startswith("bad_params")
    assert t.calls == []
    assert seen == []


# --- the post -----------------------------------------------------------------------------


def test_tags_resolve_by_exact_name_and_the_unknown_ones_are_listed():
    t = ForumTransport()
    res = post(t, thread_name="A post", tags=["Jetson news", "Nope"])
    assert t.thread_body()["applied_tags"] == ["5001"]
    assert res.output["tags_missing"] == ["Nope"]


def test_a_tag_name_differing_in_case_is_missing():
    t = ForumTransport()
    res = post(t, thread_name="A post", tags=["jetson news"])
    assert t.thread_body()["applied_tags"] == []
    assert res.output["tags_missing"] == ["jetson news"]


def test_a_short_post_is_one_thread_post_with_its_outputs():
    t = ForumTransport()
    res = post(t, thread_name="A post")
    assert res.output == {
        "message_id": "m-start",
        "thread_id": THREAD,
        "reply_ids": [],
        "tags_missing": [],
    }
    assert len(t.posts()) == 1
    assert t.thread_body()["message"]["content"] == "hello forum"
    assert t.thread_body()["name"] == "A post"


def test_the_thread_post_suppresses_mass_mentions():
    t = ForumTransport()
    post(t, thread_name="A post", text="hey @everyone")
    assert t.thread_body()["message"]["allowed_mentions"] == {"parse": []}


def _long_text():
    lines = [f"line {i:03d} " + "x" * 80 for i in range(50)]  # 50 lines of 89 characters
    return "\n".join(lines)


def test_a_long_text_starts_the_thread_with_at_most_max_content():
    text = _long_text()
    assert len(text) > 4400
    t = ForumTransport()
    post(t, thread_name="A post", text=text)
    assert len(t.thread_body()["message"]["content"]) <= MAX_CONTENT


def test_a_long_text_posts_the_rest_as_replies_to_the_thread():
    t = ForumTransport()
    res = post(t, thread_name="A post", text=_long_text())
    replies = t.posts()[1:]
    assert replies
    assert all(url.endswith(f"/channels/{THREAD}/messages") for url, _ in replies)
    assert res.output["reply_ids"] == [f"m-{i}" for i in range(1, len(replies) + 1)]


def test_every_reply_fits_max_content():
    t = ForumTransport()
    post(t, thread_name="A post", text=_long_text())
    assert all(len(body["content"]) <= MAX_CONTENT for _, body in t.posts()[1:])


def test_the_pieces_join_back_to_the_text():
    text = _long_text()
    t = ForumTransport()
    post(t, thread_name="A post", text=text)
    pieces = [t.thread_body()["message"]["content"]]
    pieces += [body["content"] for _, body in t.posts()[1:]]
    assert "".join(pieces) == text


def test_a_long_text_breaks_at_line_ends():
    t = ForumTransport()
    post(t, thread_name="A post", text=_long_text())
    assert t.thread_body()["message"]["content"].endswith("\n")


def test_a_single_line_longer_than_max_content_is_cut_hard():
    text = "y" * (MAX_CONTENT * 2 + 10)
    t = ForumTransport()
    post(t, thread_name="A post", text=text)
    pieces = [t.thread_body()["message"]["content"]]
    pieces += [body["content"] for _, body in t.posts()[1:]]
    assert [len(p) for p in pieces] == [MAX_CONTENT, MAX_CONTENT, 10]
    assert "".join(pieces) == text


def test_a_long_thread_name_is_cut_at_a_word():
    name = " ".join(["word"] * 28)  # 139 characters
    t = ForumTransport()
    post(t, thread_name=name + "s")
    sent = t.thread_body()["name"]
    assert len(sent) <= 100
    assert name.startswith(sent)
    assert sent.endswith("word")


def test_a_long_thread_name_without_spaces_is_cut_hard():
    t = ForumTransport()
    post(t, thread_name="z" * 140)
    assert t.thread_body()["name"] == "z" * 100


# --- failures ------------------------------------------------------------------------------


def test_a_reply_failing_after_the_thread_exists_is_not_retryable():
    t = ForumTransport(reply_statuses=[200, 503])
    res = post(t, thread_name="A post", text=_long_text())
    assert res.outcome == FAILED
    assert not res.retryable


def test_a_reply_failure_names_the_thread_and_the_replies_posted():
    t = ForumTransport(reply_statuses=[200, 503])
    res = post(t, thread_name="A post", text=_long_text())
    assert THREAD in res.error
    assert "m-1" in res.error


def test_a_reply_failure_stops_the_replies():
    t = ForumTransport(reply_statuses=[503])
    post(t, thread_name="A post", text=_long_text())
    assert len(t.posts()) == 2


def _three_piece_text():
    return "a" * MAX_CONTENT + "b" * MAX_CONTENT + "c"


def _three_piece():
    return {"thread_name": "A post", "text": _three_piece_text()}


def test_a_rate_limited_reply_is_sent_again_after_retry_after():
    t, slept = ForumTransport(reply_statuses=[429]), []
    res = post(t, sleep=slept.append, thread_name="A post", text=_three_piece_text())
    assert res.outcome == "completed"


def test_a_rate_limited_reply_waits_retry_after():
    t, slept = ForumTransport(reply_statuses=[429]), []
    post(t, sleep=slept.append, thread_name="A post", text=_three_piece_text())
    assert slept == [0.5]


def test_a_rate_limited_reply_is_resent_once_and_kept_in_order():
    t = ForumTransport(reply_statuses=[429])
    post(t, thread_name="A post", text=_three_piece_text())
    replies = [body["content"][0] for url, body in t.posts() if not url.endswith("/threads")]
    assert replies == ["b", "b", "c"]


def test_a_rate_limited_reply_lists_every_reply_posted():
    t = ForumTransport(reply_statuses=[429])
    res = post(t, thread_name="A post", text=_three_piece_text())
    assert res.output["reply_ids"] == ["m-2", "m-3"]


def test_a_long_retry_after_waits_at_most_ten_seconds():
    t, slept = ForumTransport(reply_statuses=[429], retry_after=60), []
    post(t, sleep=slept.append, thread_name="A post", text=_three_piece_text())
    assert slept == [10.0]


def test_a_rate_limit_without_retry_after_waits_a_second():
    t, slept = ForumTransport(reply_statuses=[429], retry_after=None), []
    post(t, sleep=slept.append, thread_name="A post", text=_three_piece_text())
    assert slept == [1.0]


def test_a_step_waits_out_at_most_three_rate_limits():
    t, slept = ForumTransport(reply_statuses=[429, 429, 200, 429, 429]), []
    post(t, sleep=slept.append, thread_name="A post", text=_three_piece_text())
    assert len(slept) == 3


def test_a_fourth_rate_limit_fails_without_retry():
    t = ForumTransport(reply_statuses=[429, 429, 200, 429, 429])
    res = post(t, thread_name="A post", text=_three_piece_text())
    assert (res.outcome, res.retryable) == (FAILED, False)


def test_a_fourth_rate_limit_names_the_replies_posted():
    t = ForumTransport(reply_statuses=[429, 429, 200, 429, 429])
    res = post(t, thread_name="A post", text=_three_piece_text())
    assert "m-3" in res.error


def test_a_wait_past_the_deadline_is_not_slept():
    t, slept = ForumTransport(reply_statuses=[429], retry_after=8), []
    post(t, sleep=slept.append, deadline=NOW + timedelta(seconds=5), **_three_piece())
    assert slept == []


def test_a_wait_past_the_deadline_fails_without_retry():
    t = ForumTransport(reply_statuses=[429], retry_after=8)
    res = post(t, deadline=NOW + timedelta(seconds=5), **_three_piece())
    assert (res.outcome, res.retryable) == (FAILED, False)


def test_a_wait_past_the_deadline_names_the_thread():
    t = ForumTransport(reply_statuses=[429], retry_after=8)
    res = post(t, deadline=NOW + timedelta(seconds=5), **_three_piece())
    assert THREAD in res.error


def test_a_wait_past_the_deadline_says_so():
    t = ForumTransport(reply_statuses=[429], retry_after=8)
    res = post(t, deadline=NOW + timedelta(seconds=5), **_three_piece())
    assert "deadline" in res.error


def test_a_wait_within_the_deadline_is_slept():
    t, slept = ForumTransport(reply_statuses=[429], retry_after=2), []
    post(t, sleep=slept.append, deadline=NOW + timedelta(seconds=5), **_three_piece())
    assert slept == [2]


def test_the_waits_add_up_against_the_deadline():
    now = [NOW]

    def sleep(seconds):
        now[0] += timedelta(seconds=seconds)

    t = ForumTransport(reply_statuses=[429, 429], retry_after=3)
    p = port(t, sleep=sleep, clock=lambda: now[0])
    params = {"channel": FORUM, **_three_piece()}
    p.invoke(params, "k", NOW + timedelta(seconds=5), context=ctx())
    assert now[0] == NOW + timedelta(seconds=3)


# --- a forum that requires a tag ------------------------------------------------------------

REQUIRE_TAG = 1 << 4


def test_a_required_tag_with_no_match_fails_bad_params():
    t = ForumTransport(flags=REQUIRE_TAG)
    res = post(t, thread_name="A post", tags=["Nope"])
    assert res.error.startswith("bad_params")


def test_a_required_tag_with_no_match_posts_nothing():
    t = ForumTransport(flags=REQUIRE_TAG)
    post(t, thread_name="A post", tags=["Nope"])
    assert t.posts() == []


def test_a_required_tag_with_no_match_names_tags_missing():
    t = ForumTransport(flags=REQUIRE_TAG)
    res = post(t, thread_name="A post", tags=["Nope"])
    assert "tags_missing" in res.error


def test_a_required_tag_with_no_match_names_the_missing_tag():
    t = ForumTransport(flags=REQUIRE_TAG)
    res = post(t, thread_name="A post", tags=["Nope"])
    assert "Nope" in res.error


def test_a_required_tag_with_no_tags_given_fails_bad_params():
    t = ForumTransport(flags=REQUIRE_TAG)
    res = post(t, thread_name="A post")
    assert (res.error.split(":")[0], res.retryable) == ("bad_params", False)


def test_a_required_tag_with_a_match_posts():
    t = ForumTransport(flags=REQUIRE_TAG)
    res = post(t, thread_name="A post", tags=["Jetson news", "Nope"])
    assert res.output["tags_missing"] == ["Nope"]


def test_other_flags_do_not_require_a_tag():
    t = ForumTransport(flags=2)
    assert post(t, thread_name="A post", tags=["Nope"]).outcome == "completed"


def test_a_thread_post_timeout_is_an_unknown_outcome_not_retried():
    t = ForumTransport(thread_timeout=True)
    res = post(t, thread_name="A post")
    assert res.outcome == FAILED
    assert not res.retryable
    assert "outcome unknown" in res.error


def test_a_thread_post_5xx_is_not_retried():
    t = ForumTransport(thread_status=502)
    res = post(t, thread_name="A post")
    assert not res.retryable


def test_a_thread_post_5xx_says_the_outcome_is_unknown():
    t = ForumTransport(thread_status=502)
    res = post(t, thread_name="A post")
    assert "outcome unknown" in res.error


def test_a_thread_post_connection_reset_is_not_retried():
    t = ForumTransport(thread_exc=ConnectionResetError(104, "reset"))
    res = post(t, thread_name="A post")
    assert not res.retryable


def test_a_thread_post_refused_connection_stays_retryable():
    t = ForumTransport(thread_exc=ConnectionRefusedError(111, "refused"))
    res = post(t, thread_name="A post")
    assert res.retryable


def test_a_thread_post_429_stays_retryable():
    t = ForumTransport(thread_status=429)
    res = post(t, thread_name="A post")
    assert res.retryable


def test_a_channel_read_5xx_stays_retryable_without_a_post():
    class Down(ForumTransport):
        def __call__(self, url, data, headers, timeout, **kw):
            self.calls.append((url, None))
            return 503, {}, {}

    t = Down()
    res = post(t, thread_name="A post")
    assert res.retryable
    assert t.posts() == []


def test_errors_never_carry_the_token():
    t = ForumTransport(reply_statuses=[503])
    res = post(t, thread_name="A post", text=_long_text())
    assert "tok-xyz" not in res.error


# --- without thread_name nothing changes ---------------------------------------------------


def test_without_thread_name_the_channel_post_is_unchanged():
    t = ForumTransport()
    res = post(t, channel=TEXT_CHANNEL)
    assert res.output == {"message_id": "m-1"}
    assert len(t.calls) == 1
    assert t.calls[0][0].endswith(f"/channels/{TEXT_CHANNEL}/messages")
