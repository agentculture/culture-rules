"""``_unfenced`` replaced the fence regex (Sonar S8786); each case is what the regex gave."""

import pytest

from culture_rules.node.actions.transcript_render import _unfenced

_CASES = [
    ('```json\n{"summary": "a", "word": "b"}\n```', '{"summary": "a", "word": "b"}'),
    ('```\n{"a": 1}\n```', '{"a": 1}'),
    ('```JSON {"a": 1}```', '{"a": 1}'),
    ("``````", ""),
    ("```", "```"),
    ("```json```", ""),
    ('```json\n{"a": "x ``` y"}\n```', '{"a": "x ``` y"}'),
    ('{"a": 1}', '{"a": 1}'),
    ('```json\n{"a": 1}', '```json\n{"a": 1}'),
    ('{"a": 1}\n```', '{"a": 1}\n```'),
    ("```py3\nx\n```", "3\nx"),
    ("```" + "a" * 5000 + "\n" + " " * 5000 + "```", ""),
]


@pytest.mark.parametrize(("text", "expected"), _CASES)
def test_unfenced_gives_what_the_fence_regex_gave(text: str, expected: str) -> None:
    assert _unfenced(text) == expected
