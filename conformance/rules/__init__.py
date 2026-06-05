"""Pluggable rule framework for the conformance "lens" (AXIS 2).

A *rule* inspects one unit of evidence — a spec endpoint, the whole spec, or a
runtime response — and either returns a :class:`core.results.Finding` describing a
design/implementation defect, or ``None`` when the rule is satisfied.

The lens is extended by *adding rule modules*: define one or more objects that
satisfy the :class:`Rule` protocol and call :func:`register` (or drop a module
that does so and let :func:`load_builtin_rules` / discovery pick it up). Callers
(``conformance.static`` / ``conformance.runtime``) iterate :func:`rules` and emit
whatever findings come back, so new checks need no edits to the engines.

Design intent:
  * ``Rule`` is a *structural* protocol (duck-typed): anything with ``id``,
    ``severity`` and a ``check(context) -> Finding | None`` qualifies. Both plain
    objects and small dataclasses/functions-wrapped-in-:class:`FunctionRule` work.
  * The ``context`` passed to ``check`` is deliberately loose (an endpoint dict, a
    whole-spec dict, or a runtime response record) so the same registry can host
    static and runtime rules; a rule advertises what it consumes via ``scope``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from core.results import Finding

# rule scopes — what kind of context a rule's check() expects
SCOPE_ENDPOINT = "endpoint"   # check(ctx) where ctx is a single endpoint doc dict
SCOPE_SPEC = "spec"           # check(ctx) where ctx is the whole api_docs dict
SCOPE_RESPONSE = "response"   # check(ctx) where ctx is a runtime response record

# severities mirror core.results.Finding (red | yellow | green)
RED, YELLOW, GREEN = "red", "yellow", "green"


@runtime_checkable
class Rule(Protocol):
    """Structural contract every conformance rule satisfies.

    Attributes:
        id:       stable rule identifier, e.g. ``"method-verb.mismatch"``; becomes
                  ``Finding.rule_id``.
        severity: default severity for findings this rule emits (red|yellow|green).
        scope:    one of ``SCOPE_ENDPOINT`` / ``SCOPE_SPEC`` / ``SCOPE_RESPONSE``;
                  tells the engine which context to feed ``check``.

    Method:
        check(context) -> Finding | None
            Inspect the context; return a Finding when a defect is present, else
            None. Implementations must be pure/read-only — never perform I/O.
    """

    id: str
    severity: str
    scope: str

    def check(self, context: Any) -> Optional[Finding]:
        ...


# --- registry ---------------------------------------------------------------
_REGISTRY: dict[str, Rule] = {}


def register(rule: Rule) -> Rule:
    """Register a rule instance (idempotent by ``rule.id``). Returns the rule so
    it can be used as a decorator on :class:`FunctionRule`-style factories."""
    _REGISTRY[rule.id] = rule
    return rule


def rules(scope: str | None = None) -> list[Rule]:
    """All registered rules, optionally filtered to a single ``scope``."""
    out = list(_REGISTRY.values())
    if scope is not None:
        out = [r for r in out if getattr(r, "scope", SCOPE_ENDPOINT) == scope]
    return out


def clear() -> None:
    """Drop all registered rules (test helper)."""
    _REGISTRY.clear()


@dataclass
class FunctionRule:
    """Adapt a plain ``check`` function into a :class:`Rule`.

    Lets a rule module express a check as a function while still satisfying the
    protocol::

        @register
        class _ : ...          # or:
        register(FunctionRule("my.rule", YELLOW, SCOPE_ENDPOINT, my_check_fn))
    """

    id: str
    severity: str
    scope: str
    fn: Callable[[Any], Optional[Finding]]

    def check(self, context: Any) -> Optional[Finding]:
        return self.fn(context)


# --- built-in rule plugins --------------------------------------------------
# The per-endpoint STATIC design/docs checks ported from the conformance
# session's ``tools/analyze_docs.py`` live in :mod:`conformance.rules.docs`;
# importing that module registers them via :func:`register`. Whole-spec
# aggregates (path collisions, validation-discoverability, model-level checks)
# stay in :mod:`conformance.static` because they emit many findings per scan and
# need cross-endpoint context that the one-context/one-Finding ``Rule`` protocol
# cannot carry.


def load_builtin_rules() -> None:
    """Register the built-in rules (idempotent by ``rule.id``).

    Imported lazily to avoid an import cycle (``rules.docs`` imports this module
    for :class:`FunctionRule` and :func:`register`).
    """
    from conformance.rules import docs as _docs  # noqa: F401
    _docs.load_docs_rules()


# Register built-ins on import so `from conformance import rules; rules.rules()`
# is populated out of the box. Importing is side-effect free beyond this.
load_builtin_rules()


__all__ = [
    "Rule", "FunctionRule", "Finding",
    "register", "rules", "clear", "load_builtin_rules",
    "SCOPE_ENDPOINT", "SCOPE_SPEC", "SCOPE_RESPONSE",
    "RED", "YELLOW", "GREEN",
]
