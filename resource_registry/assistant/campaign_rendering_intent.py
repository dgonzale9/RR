"""Identify campaign-rendering requests before Registry exploration intercepts them."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

CAMPAIGN_RENDERING_BIF_NAME = "creative_assembly_create_campaign_asset"
EXPLORATION_BIF_NAME = "resource_registry_explore_dataset"


class CampaignRenderingIntentPolicy:
    """Recognize campaign asset creation requests from their rendering semantics."""

    _CREATION_PATTERN = re.compile(
        r"\b(?:create|generate|make|render|design|build|crear|crea|genera(?:r)?|"
        r"renderiza(?:r)?|diseña(?:r)?)\b",
        flags=re.IGNORECASE,
    )
    _CAMPAIGN_OUTPUT_PATTERN = re.compile(
        r"\b(?:campaign\s+(?:banner|creative|asset)|(?:banner|creative|asset)\s+"
        r"(?:for|de la|de el)?\s*campaign|banner|creative|svg|pieza\s+creativa|anuncio)\b",
        flags=re.IGNORECASE,
    )
    _IMAGE_ROLE_PATTERN = re.compile(r"\b(?:logo|background|fondo)\b", flags=re.IGNORECASE)
    _COPY_ROLE_PATTERN = re.compile(
        r"\b(?:headline|cta|call\s+to\s+action|button\s+text|title\s+copy|titular|"
        r"llamado\s+a\s+la\s+acci[oó]n|texto\s+del\s+bot[oó]n)\b",
        flags=re.IGNORECASE,
    )
    _LAYOUT_ROLE_PATTERN = re.compile(
        r"\b(?:logo\s+placement|background\s+focus|logo\s+(?:placement|position)|"
        r"background\s+(?:focus|position)|posici[oó]n\s+del\s+logo|"
        r"ubicaci[oó]n\s+del\s+logo|enfoque\s+del\s+fondo)\b",
        flags=re.IGNORECASE,
    )

    def is_campaign_rendering_intent(self, statement: str) -> bool:
        """Return whether a statement requests a campaign-rendering artifact.

        Args:
            statement: Raw user request.

        Returns:
            True only when creation language combines with a campaign-output request or at least
            two explicit rendering roles.
        """
        if not self._CREATION_PATTERN.search(statement):
            return False

        rendering_role_count = sum(
            pattern.search(statement) is not None
            for pattern in (
                self._IMAGE_ROLE_PATTERN,
                self._COPY_ROLE_PATTERN,
                self._LAYOUT_ROLE_PATTERN,
            )
        )
        return (
            self._CAMPAIGN_OUTPUT_PATTERN.search(statement) is not None or rendering_role_count >= 2
        )

    def should_exclude_exploration_tool(self, *, persona: object | None, statement: str) -> bool:
        """Return whether campaign rendering must exclude generic exploration from tool selection.

        Args:
            persona: Active Persona carrying assigned Built-In Functions.
            statement: Raw user request.

        Returns:
            True when the campaign BIF is available and the request is a rendering intent.
        """
        return self.is_campaign_rendering_intent(statement) and self._has_campaign_renderer(persona)

    @staticmethod
    def filter_exploration_tool(tools: Sequence[dict[str, object]]) -> list[dict[str, object]]:
        """Remove only the generic Registry exploration BIF from a rendered tool list.

        Args:
            tools: Provider-specific function-tool definitions already admitted for a Persona.

        Returns:
            The tool definitions excluding only ``resource_registry_explore_dataset``.
        """
        return [
            tool for tool in tools if not CampaignRenderingIntentPolicy._is_exploration_tool(tool)
        ]

    @staticmethod
    def _has_campaign_renderer(persona: object | None) -> bool:
        relationship_manager = getattr(persona, "builtinfunctionpersonarelationship_set", None)
        if relationship_manager is None:
            return False
        return bool(
            relationship_manager.filter(
                builtin_function__name__in=(CAMPAIGN_RENDERING_BIF_NAME,),
            ).exists(),
        )

    @staticmethod
    def _is_exploration_tool(tool: Mapping[str, object]) -> bool:
        function = tool.get("function")
        if isinstance(function, Mapping):
            return function.get("name") == EXPLORATION_BIF_NAME
        return tool.get("name") == EXPLORATION_BIF_NAME
