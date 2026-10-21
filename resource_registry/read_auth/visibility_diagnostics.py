from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class VisibilityRuleMatch:
    rule_type: str
    rule_name: str
    matched: bool
    action: Literal["visible", "masked", "hidden"]
    precedence: int
    reason: str
    source: str
    policy_source: str | None
    policy_revision: int | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisibilityPrecedenceTrace:
    field_name: str
    field_type: Literal["schema_column", "profile_metric"]
    final_action: Literal["visible", "masked", "hidden"]
    winning_rule: VisibilityRuleMatch
    matched_rules: tuple[VisibilityRuleMatch, ...]
    suppressed_rules: tuple[VisibilityRuleMatch, ...]
    conflict_detected: bool
    conflict_summary: str | None


@dataclass(frozen=True)
class VisibilityDiagnosticsReport:
    dataset_id: str | None
    policy_key: str
    policy_source: str
    policy_revision: int | None
    traces: tuple[VisibilityPrecedenceTrace, ...]
    summary: dict[str, int]


class VisibilityRuleDiagnosticsService:
    """Builds deterministic precedence traces for visibility rule matches.

    Precedence order (lower wins):
    1. security removal / credential/raw/preview removal
    2. explicit hidden field rule
    3. hidden classification rule
    4. allowed_columns / allowed_metrics restriction
    5. explicit masked field rule
    6. masked classification rule
    7. full-read scope
    8. masked-read scope
    9. role default
    10. system default
    """

    def build_trace(  # noqa: PLR0913
        self,
        *,
        field_name: str,
        field_type: str,
        policy_source: str,
        policy_revision: int | None,
        flags: dict[str, bool],
        matched_classifications: tuple[str, ...] = (),
    ) -> VisibilityPrecedenceTrace:
        rules = self._rule_matches(
            policy_source=policy_source,
            policy_revision=policy_revision,
            flags=flags,
            matched_classifications=matched_classifications,
        )
        matched = tuple(rule for rule in rules if rule.matched)
        winning = matched[0] if matched else rules[-1]
        suppressed = tuple(rule for rule in matched if rule is not winning)

        distinct_actions = {rule.action for rule in matched}
        conflict_detected = len(distinct_actions) > 1
        conflict_summary = None
        if conflict_detected:
            conflict_summary = (
                f"Multiple matched rules with different actions; "
                f"{winning.rule_name} won by precedence."
            )

        return VisibilityPrecedenceTrace(
            field_name=field_name,
            field_type=field_type,  # type: ignore[arg-type]
            final_action=winning.action,
            winning_rule=winning,
            matched_rules=matched,
            suppressed_rules=suppressed,
            conflict_detected=conflict_detected,
            conflict_summary=conflict_summary,
        )

    def _rule_matches(
        self,
        *,
        policy_source: str,
        policy_revision: int | None,
        flags: dict[str, bool],
        matched_classifications: tuple[str, ...],
    ) -> tuple[VisibilityRuleMatch, ...]:
        classification_reason = "hidden_by_classification"
        masked_classification_reason = "masked_by_classification"
        if flags.get("unknown_classification", False):
            classification_reason = "unknown_classification_treated_sensitive"
            masked_classification_reason = "unknown_classification_treated_sensitive"
        return (
            VisibilityRuleMatch(
                rule_type="security",
                rule_name="security_removal",
                matched=flags.get("security_removal", False),
                action="hidden",
                precedence=1,
                reason="removed_sensitive_key",
                source="security",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="field",
                rule_name="explicit_hidden",
                matched=flags.get("explicit_hidden", False),
                action="hidden",
                precedence=2,
                reason="hidden_by_column_name",
                source="policy",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="classification",
                rule_name="hidden_classification",
                matched=flags.get("hidden_classification", False),
                action="hidden",
                precedence=3,
                reason=classification_reason,
                source="policy",
                policy_source=policy_source,
                policy_revision=policy_revision,
                metadata={"classifications": list(matched_classifications)},
            ),
            VisibilityRuleMatch(
                rule_type="allowlist",
                rule_name="allowed_restriction",
                matched=flags.get("restricted_allowlist", False),
                action="hidden",
                precedence=4,
                reason="restricted_by_allowed_columns",
                source="policy",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="field",
                rule_name="explicit_masked",
                matched=flags.get("explicit_masked", False),
                action="masked",
                precedence=5,
                reason="masked_by_column_name",
                source="policy",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="classification",
                rule_name="masked_classification",
                matched=flags.get("masked_classification", False),
                action="masked",
                precedence=6,
                reason=masked_classification_reason,
                source="policy",
                policy_source=policy_source,
                policy_revision=policy_revision,
                metadata={"classifications": list(matched_classifications)},
            ),
            VisibilityRuleMatch(
                rule_type="scope",
                rule_name="full_read_scope",
                matched=flags.get("full_scope", False),
                action="visible",
                precedence=7,
                reason="allowed_by_scope",
                source="scope",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="scope",
                rule_name="masked_read_scope",
                matched=flags.get("masked_scope", False),
                action="masked",
                precedence=8,
                reason="allowed_by_scope",
                source="scope",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="role",
                rule_name="role_default",
                matched=True,
                action="visible" if flags.get("role_allows_visible", False) else "hidden",
                precedence=9,
                reason=(
                    "allowed_by_role"
                    if flags.get("role_allows_visible", False)
                    else "default_hidden"
                ),
                source="role",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
            VisibilityRuleMatch(
                rule_type="default",
                rule_name="system_default",
                matched=True,
                action="visible",
                precedence=10,
                reason="default_visible",
                source="system",
                policy_source=policy_source,
                policy_revision=policy_revision,
            ),
        )


    def summarize(self, traces: tuple[VisibilityPrecedenceTrace, ...]) -> dict[str, int]:
        hidden_count = 0
        masked_count = 0
        visible_count = 0
        conflicts = 0
        for trace in traces:
            if trace.final_action == "hidden":
                hidden_count += 1
            elif trace.final_action == "masked":
                masked_count += 1
            else:
                visible_count += 1
            if trace.conflict_detected:
                conflicts += 1
        return {
            "total_fields": len(traces),
            "conflicts_detected": conflicts,
            "hidden_count": hidden_count,
            "masked_count": masked_count,
            "visible_count": visible_count,
        }
