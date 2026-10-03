"""Secret references, resolved at run time on the executing host.

Definitions, exports and logs only ever carry a *reference* (``grant:<NAME>``); the value is
fetched from the operator's ``grant`` CLI on the host that executes the step and is never
written anywhere. (Approved deviation d2: ``grant`` replaced ``shushu`` as the secret store.)

Two ways to use a secret:

* :func:`resolve` runs ``grant get NAME`` (argv list, no shell) and returns the value. It
  refuses hidden secrets, as grant does. Pass a :class:`Redactor` to register the value so
  later log/export text can be scrubbed.
* :func:`run_with_secrets` / :func:`grant_run_argv` run a step's subprocess as
  ``grant run --inject VAR=NAME -- cmd args``, so the value never enters this process
  (the way to use hidden secrets).

:func:`assert_refs_only` guards definitions: any secret-looking parameter must be a reference.
Zero third-party dependencies; only the stdlib ``subprocess`` is used.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - only used to call the operator's grant CLI
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = [
    "GRANT_SCHEME",
    "Redactor",
    "SecretError",
    "assert_refs_only",
    "grant_run_argv",
    "is_secret_ref",
    "parse_ref",
    "resolve",
    "resolve_or_literal",
    "run_with_secrets",
]

GRANT_SCHEME = "grant"
_REF_RE = re.compile(r"^grant:([A-Za-z0-9][A-Za-z0-9._/-]*)$")
_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# A key is secret-bearing when one of its whole segments (split on "_", "-" and camelCase
# humps) names a secret - so "github_token" and "apiKey" are, "author" and "auth_mode" are
# not - unless a segment marks it as a budget/limit ("max_tokens", "token_budget").
_SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:secret|token|password|passwd|api[_-]?key|credential|private[_-]?key)s?"
    r"(?:[_-]|$)",
    re.IGNORECASE,
)
_BUDGET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:budget|limit|max|min|count|num|quota|pct|warn)(?:[_-]|$)", re.IGNORECASE
)
_CAMEL_HUMP_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_MIN_REDACT_LEN = 4
_TIMEOUT_S = 30


def _is_secret_key(key: str) -> bool:
    """Whether a param named ``key`` must hold a secret reference (see the regexes)."""
    normalised = _CAMEL_HUMP_RE.sub("_", key)
    return bool(_SECRET_KEY_RE.search(normalised)) and not _BUDGET_KEY_RE.search(normalised)


class SecretError(Exception):
    """A secret reference is malformed, unresolvable, or a literal where a reference belongs.

    Messages carry the reference and never a secret value.
    """


def is_secret_ref(value: object) -> bool:
    """True for a well-formed ``grant:<NAME>`` reference."""
    return isinstance(value, str) and _REF_RE.match(value) is not None


def parse_ref(ref: str) -> tuple[str, str]:
    """Return ``("grant", NAME)`` for a reference, else raise :class:`SecretError`."""
    match = _REF_RE.match(ref) if isinstance(ref, str) else None
    if match is None:
        raise SecretError("not a secret reference (expected 'grant:<NAME>')")
    return GRANT_SCHEME, match.group(1)


class Redactor:
    """Collects resolved values and masks them in text or nested structures."""

    MASK = "***"

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        """Register a value for redaction (very short values are ignored: too noisy)."""
        if isinstance(value, str) and len(value) >= _MIN_REDACT_LEN:
            self._values.add(value)

    def redact(self, obj: Any) -> Any:
        """Return ``obj`` with every registered value replaced by ``***``."""
        if isinstance(obj, str):
            for value in sorted(self._values, key=len, reverse=True):
                obj = obj.replace(value, self.MASK)
            return obj
        if isinstance(obj, Mapping):
            return {k: self.redact(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact(v) for v in obj]
        return obj


def _grant_get(name: str, run: Callable[..., Any]) -> str:
    done = run(  # nosec B603 B607 - fixed argv, no shell
        ["grant", "get", name], capture_output=True, text=True, check=True, timeout=_TIMEOUT_S
    )
    return done.stdout.strip()


def resolve(
    ref: str,
    runner: Callable[[str], str] | None = None,
    *,
    redactor: Redactor | None = None,
    _run: Callable[..., Any] | None = None,
) -> str:
    """Resolve a ``grant:<NAME>`` reference to its value, on this host, at call time.

    ``runner(name) -> value`` replaces the grant call (tests); ``_run`` replaces
    ``subprocess.run``. Failures raise :class:`SecretError` naming the reference only.
    """
    _, name = parse_ref(ref)
    try:
        value = runner(name) if runner is not None else _grant_get(name, _run or subprocess.run)
    except SecretError:
        raise
    except Exception as exc:  # noqa: BLE001 - never echo the underlying text: it may hold a value
        raise SecretError(f"cannot resolve secret reference {ref}: {type(exc).__name__}") from exc
    if redactor is not None:
        redactor.add(value)
    return value


def resolve_or_literal(value: str, runner: Callable[[str], str] | None = None, **kw: Any) -> str:
    """Resolve ``grant:`` references; return anything else untouched (non-secret config)."""
    if isinstance(value, str) and value.startswith(GRANT_SCHEME + ":"):
        return resolve(value, runner, **kw)
    return value


def grant_run_argv(argv: Sequence[str], injections: Mapping[str, str]) -> list[str]:
    """Build ``grant run --inject VAR=NAME ... -- argv`` from env-var -> reference pairs."""
    out = ["grant", "run"]
    for var, ref in injections.items():
        if not _VAR_RE.match(var):
            raise SecretError(f"invalid environment variable name {var!r}")
        _, name = parse_ref(ref)
        out += ["--inject", f"{var}={name}"]
    return [*out, "--", *argv]


def run_with_secrets(
    argv: Sequence[str],
    injections: Mapping[str, str],
    *,
    _run: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Run ``argv`` under ``grant run`` so secrets reach only the child's environment."""
    full = grant_run_argv(argv, injections)
    return (_run or subprocess.run)(  # nosec B603 B607 - argv list, no shell
        full, capture_output=True, text=True, **kwargs
    )


def assert_refs_only(params: Any, path: str = "") -> None:
    """Raise :class:`SecretError` if a secret-looking key holds a literal instead of a reference.

    Walks nested mappings and lists; the error names the offending path, never the value.
    """
    if isinstance(params, Mapping):
        for key, value in params.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(value, str) and _is_secret_key(str(key)):
                if value and not is_secret_ref(value):
                    raise SecretError(f"{here}: secret must be a 'grant:<NAME>' reference")
            else:
                assert_refs_only(value, here)
    elif isinstance(params, (list, tuple)):
        for i, item in enumerate(params):
            assert_refs_only(item, f"{path}[{i}]")
