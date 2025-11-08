"""Shared admin UI helpers."""

from __future__ import annotations

import abc
import logging

import discord

logger = logging.getLogger(__name__)


class LazyInitView(discord.ui.View, metaclass=abc.ABCMeta):
    """Base view that populates components on first use."""

    def __init__(self, *, timeout: Optional[float] = 300) -> None:
        super().__init__(timeout=timeout)
        self._is_prepared = False

    async def prepare(self) -> None:
        """Populate the view if it has not been populated yet."""
        if self._is_prepared:
            return

        try:
            await self.populate()
            self._is_prepared = True
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to populate view %s: %s", self.__class__.__name__, exc, exc_info=True)
            raise

    @abc.abstractmethod
    async def populate(self) -> None:
        """Populate the view with components. Must be implemented by subclasses."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self._is_prepared:
            await self.prepare()
        return True
