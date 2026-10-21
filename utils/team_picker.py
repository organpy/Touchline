"""Team select that works for any number of teams.

Discord selects hold at most 25 options. ``TeamSelect`` keeps the whole flow inside one select:

* 25 teams or fewer: a flat list of teams.
* More than that, across several divisions: the select first lists the divisions (tier order);
  choosing one shows that division's teams.
* A division (or a single-division league) with too many teams is paged with
  "Previous page" / "Next page" options.

Team options use the plain team value (id or code), so an ``on_pick`` callback can keep reading
``interaction.data['values'][0]``. Navigation options start with ``__`` and are handled here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

import discord

MAX_OPTIONS = 25
NAV_PREFIX = "__"
NAV_DIVISION = "__div:"
NAV_ALL = "__all"
NAV_PREV = "__prev"
NAV_NEXT = "__next"
UNASSIGNED = "none"


@dataclass(frozen=True)
class PickerEntry:
    """One pickable team."""

    value: str
    label: str
    description: Optional[str] = None
    emoji: Optional[str] = None
    division_id: Optional[str] = None


@dataclass(frozen=True)
class PickerDivision:
    id: str
    label: str
    tier: int = 0


@dataclass(frozen=True)
class PickerOption:
    value: str
    label: str
    description: Optional[str] = None
    emoji: Optional[str] = None


def entry_from_team(team: Dict[str, Any], *, value_key: str = "id", description: Optional[str] = None) -> PickerEntry:
    """Picker entry for a team dict (as returned by the caches / ``LeagueCache.enrich_team``)."""
    code = (team.get("code") or "").upper()
    name = team.get("name") or code or "Unknown"
    label = f"{name} ({code})" if code and code != name.upper() else name
    if description is None:
        description = team.get("league_name") or team.get("division") or None
    return PickerEntry(
        value=str(team.get(value_key) or ""),
        label=label,
        description=description,
        emoji=team.get("emoji") or None,
        division_id=team.get("division_id"),
    )


def entries_from_cached_teams(teams: Iterable[Any]) -> List[PickerEntry]:
    """Picker entries (valued by team code) for RosterCache ``CachedTeam`` objects, sorted by name."""
    ordered = sorted(teams, key=lambda t: (getattr(t, "name", "") or "").lower())
    return [
        PickerEntry(
            value=t.code,
            label=t.name or t.code.upper(),
            description=t.code.upper(),
            emoji=getattr(t, "emoji", None) or None,
            division_id=getattr(t, "division_id", None),
        )
        for t in ordered
        if getattr(t, "code", None)
    ]


def divisions_from_cache() -> List[PickerDivision]:
    """Non-archived divisions in tier order, from LeagueCache."""
    from utils.league_cache import LeagueCache

    result = []
    for division in LeagueCache.divisions():
        code = (division.get("code") or "").upper()
        name = division.get("name") or code
        label = f"{code} · {name}" if code and name and name.upper() != code else (name or code)
        result.append(PickerDivision(id=str(division["id"]), label=label, tier=division.get("tier") or 0))
    return sorted(result, key=lambda d: d.tier)


def _truncate(text: Optional[str], limit: int) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_options(
    entries: List[PickerEntry],
    divisions: List[PickerDivision],
    *,
    division_id: Optional[str] = None,
    page: int = 0,
) -> List[PickerOption]:
    """The options to show for the current picker state (pure; unit tested)."""
    if len(entries) <= MAX_OPTIONS:
        return [PickerOption(e.value, e.label, e.description, e.emoji) for e in entries]

    known = {d.id for d in divisions}
    buckets: Dict[str, List[PickerEntry]] = {}
    for entry in entries:
        key = entry.division_id if entry.division_id in known else UNASSIGNED
        buckets.setdefault(key, []).append(entry)

    use_divisions = len(buckets) > 1
    if use_divisions and division_id is None:
        options = [
            PickerOption(f"{NAV_DIVISION}{d.id}", d.label, f"{len(buckets[d.id])} teams", "📂")
            for d in divisions
            if d.id in buckets
        ]
        if UNASSIGNED in buckets:
            options.append(
                PickerOption(f"{NAV_DIVISION}{UNASSIGNED}", "No division", f"{len(buckets[UNASSIGNED])} teams", "📂")
            )
        return options[:MAX_OPTIONS]

    pool = buckets.get(division_id, []) if use_divisions else entries
    back = [PickerOption(NAV_ALL, "All divisions", "Back to the division list", "↩️")] if use_divisions else []

    room = MAX_OPTIONS - len(back)
    if len(pool) <= room:
        return back + [PickerOption(e.value, e.label, e.description, e.emoji) for e in pool]

    per_page = room - 2  # keep room for previous + next
    pages = max(1, -(-len(pool) // per_page))
    page = max(0, min(page, pages - 1))
    chunk = pool[page * per_page:(page + 1) * per_page]
    options = list(back)
    if page > 0:
        options.append(PickerOption(NAV_PREV, "Previous page", f"Page {page} of {pages}", "◀️"))
    options.extend(PickerOption(e.value, e.label, e.description, e.emoji) for e in chunk)
    if page < pages - 1:
        options.append(PickerOption(NAV_NEXT, "Next page", f"Page {page + 2} of {pages}", "▶️"))
    return options


class TeamSelect(discord.ui.Select):
    """A select over any number of teams. Calls ``on_pick(interaction)`` when a team is chosen."""

    def __init__(
        self,
        entries: Iterable[PickerEntry],
        *,
        on_pick: Callable[[discord.Interaction], Awaitable[Any]],
        divisions: Optional[List[PickerDivision]] = None,
        placeholder: str = "Select team...",
        row: Optional[int] = None,
        custom_id: Optional[str] = None,
    ) -> None:
        self.entries = [e for e in entries if e.value]
        self.divisions = divisions if divisions is not None else divisions_from_cache()
        self.division_id: Optional[str] = None
        self.page = 0
        self.base_placeholder = placeholder
        self._on_pick = on_pick
        kwargs: Dict[str, Any] = {"placeholder": placeholder, "min_values": 1, "max_values": 1, "row": row}
        if custom_id:
            kwargs["custom_id"] = custom_id
        options, empty = self._render()
        super().__init__(options=options, disabled=empty, **kwargs)

    @property
    def picked(self) -> Optional[str]:
        """The chosen team value, if the last choice was a team."""
        if not self.values or self.values[0].startswith(NAV_PREFIX):
            return None
        return self.values[0]

    def _render(self) -> Tuple[List[discord.SelectOption], bool]:
        """(options, empty) for the current state."""
        options = build_options(self.entries, self.divisions, division_id=self.division_id, page=self.page)
        if not options:
            return [discord.SelectOption(label="No teams found", value=f"{NAV_PREFIX}empty")], True
        return [
            discord.SelectOption(
                label=_truncate(o.label, 100) or "?",
                value=o.value,
                description=_truncate(o.description, 100),
                emoji=o.emoji,
            )
            for o in options
        ], False

    def _division_label(self) -> Optional[str]:
        if self.division_id == UNASSIGNED:
            return "No division"
        return next((d.label for d in self.divisions if d.id == self.division_id), None)

    def apply_navigation(self, value: str) -> None:
        """Update the picker state for a navigation option and re-render."""
        if value.startswith(NAV_DIVISION):
            self.division_id = value[len(NAV_DIVISION):]
            self.page = 0
        elif value == NAV_ALL:
            self.division_id = None
            self.page = 0
        elif value == NAV_PREV:
            self.page = max(0, self.page - 1)
        elif value == NAV_NEXT:
            self.page += 1
        label = self._division_label()
        self.placeholder = f"{self.base_placeholder} ({label})" if label else self.base_placeholder
        self.options, _ = self._render()

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0] if self.values else ""
        if value.startswith(NAV_PREFIX):
            if value != f"{NAV_PREFIX}empty":
                self.apply_navigation(value)
            await interaction.response.edit_message(view=self.view)
            return
        await self._on_pick(interaction)


def team_select_for(
    teams: Iterable[Dict[str, Any]],
    *,
    on_pick: Callable[[discord.Interaction], Awaitable[Any]],
    value_key: str = "id",
    placeholder: str = "Select team...",
    row: Optional[int] = None,
    describe: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
) -> TeamSelect:
    """``TeamSelect`` for team dicts, sorted by name."""
    ordered = sorted(teams, key=lambda t: (t.get("name") or t.get("code") or "").lower())
    entries = [
        entry_from_team(t, value_key=value_key, description=describe(t) if describe else None)
        for t in ordered
    ]
    return TeamSelect(entries, on_pick=on_pick, placeholder=placeholder, row=row)
