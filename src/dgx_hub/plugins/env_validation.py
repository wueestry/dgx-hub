"""Evaluates a manifest's `[[env.validation]]` rules against resolved env
values. The only module that depends on `simpleeval` — kept out of
`manifest.py` so that module stays pydantic-only.
"""

from __future__ import annotations

from simpleeval import EvalWithCompoundTypes, InvalidExpression

from dgx_hub.plugins.manifest import PluginManifest


def evaluate_rules(
    manifest: PluginManifest,
    env_values_typed: dict[str, bool | int | str],
    variant_id: str | None,
) -> list[str]:
    """Return the `.message` of every declared rule that evaluates falsy.

    Raises ValueError (naming the offending rule) if a rule references an
    undeclared name or is otherwise malformed, instead of letting a raw
    simpleeval exception escape.
    """
    names: dict[str, object] = {**env_values_typed, "variant": variant_id or ""}
    evaluator = EvalWithCompoundTypes(names=names)
    violations: list[str] = []
    for rule in manifest.env_validation:
        try:
            result = evaluator.eval(rule.rule)
        except (InvalidExpression, SyntaxError) as exc:
            raise ValueError(f"env_validation rule {rule.rule!r} is invalid: {exc}") from exc
        if not result:
            violations.append(rule.message)
    return violations
