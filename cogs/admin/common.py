"""Shared admin UI helpers."""

from __future__ import annotations

import abc
import logging
from typing import Optional

import discord

logger = logging.getLogger(__name__)


class LazyInitView(discord.ui.View, metaclass=abc.ABCMeta):
    """Base view that populates components on first use."""

    def __init__(self, *, timeout: Optional[float] = 300) -> None:
        super().__init__(timeout=timeout)
        self._is_prepared = False
        self.original_message: Optional[discord.Message] = None

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

    def create_embed(self) -> discord.Embed:
        """Create the embed for this view. Override in subclasses."""
        return discord.Embed(title="View", color=0x2B2D31)

    async def update_display(self) -> None:
        """Update the message with the current embed and view state."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except discord.NotFound:
            logger.warning("Message not found when updating display for %s", self.__class__.__name__)
        except Exception as exc:
            logger.error("Failed to update display for %s: %s", self.__class__.__name__, exc)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self._is_prepared:
            await self.prepare()
        return True
