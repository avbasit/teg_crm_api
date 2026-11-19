"""Turn lead-form answers into lead columns.

Answers arrive as key/value pairs (website forms, ad forms) or as ``key: value``
lines inside Jaldi's Notes column. Every answer is kept in ``leads.form_answers``
(keys in snake_case); the ones listed in ``form_field_aliases`` are also copied
to real columns so they can be filtered.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import FormFieldAlias

# Lead columns an alias may point at.
CANONICAL_FIELDS = {
    "full_name",
    "email",
    "phone_raw",
    "whatsapp_e164",
    "city",
    "country_of_interest",
    "budget_text",
    "education_level",
    "english_test_status",
    "age_text",
    "has_family",
}
_KV_LINE = re.compile(r"^\s*([^:\n]{1,150}?)\s*:\s*(.*?)\s*$")
_YES = {"yes", "y", "true", "1", "yes, with family", "with family"}
_NO = {"no", "n", "false", "0", "just me", "alone", "single"}


def normalise_key(raw: str) -> str:
    """Lower-case snake_case key, without the broken-encoding prefix some exports have."""
    key = raw.strip().lower()
    key = re.sub(r"^[^a-z0-9(]+", "", key)  # e.g. "�_city" -> "city"
    return re.sub(r"\s+", "_", key)


def parse_yes_no(value: str | None) -> bool | None:
    """Yes/no answers ("Yes", "no", "true") as a boolean; anything else is unknown."""
    if value is None:
        return None
    text = value.strip().lower()
    if text in _YES or text.startswith("yes"):
        return True
    if text in _NO or text.startswith("no"):
        return False
    return None


def parse_notes(text: str | None) -> tuple[dict[str, str], str | None]:
    """Split Jaldi-style notes into form answers and the remaining free text.

    Notes count as a form only when at least two lines look like ``key: value``,
    so a one-line note such as "Called: no answer" stays a note.
    """
    if not text or not text.strip():
        return {}, None
    lines = text.splitlines()
    pairs = [(_KV_LINE.match(line), line) for line in lines]
    if sum(1 for match, _ in pairs if match) < 2:
        return {}, text.strip()
    answers: dict[str, str] = {}
    rest: list[str] = []
    for match, line in pairs:
        if match and match.group(2):
            answers[match.group(1)] = match.group(2)
        elif line.strip() and not match:
            rest.append(line.strip())
    return answers, "\n".join(rest) or None


@dataclass
class Extracted:
    """Answers mapped to lead columns, plus everything kept as form answers."""

    columns: dict[str, Any] = field(default_factory=dict)
    answers: dict[str, Any] = field(default_factory=dict)


class AliasMap:
    """The organisation's question-key → column map, loaded once per request."""

    def __init__(self, aliases: dict[str, str]) -> None:
        """Wrap a normalised raw-key → column map (use :meth:`load`)."""
        self._aliases = aliases

    @classmethod
    async def load(cls, session: AsyncSession, organization_id: uuid.UUID) -> "AliasMap":
        """Read ``form_field_aliases`` for one organisation."""
        rows = await session.execute(
            select(FormFieldAlias.raw_key, FormFieldAlias.canonical_field).where(
                FormFieldAlias.organization_id == organization_id
            )
        )
        aliases: dict[str, str] = {}
        for raw_key, canonical in rows.all():
            if canonical in CANONICAL_FIELDS:
                aliases[normalise_key(raw_key)] = canonical
        return cls(aliases)

    def column_for(self, raw_key: str) -> str | None:
        """The lead column a question feeds, if any."""
        key = normalise_key(raw_key)
        if key in CANONICAL_FIELDS:
            return key
        return self._aliases.get(key) or self._aliases.get(key.replace("_", " "))

    def extract(self, answers: dict[str, Any]) -> Extracted:
        """Map answers onto lead columns; keep all of them (snake_case keys) as answers."""
        result = Extracted()
        for raw_key, raw_value in answers.items():
            if raw_value is None:
                continue
            value = str(raw_value).strip() if not isinstance(raw_value, bool) else raw_value
            if value == "":
                continue
            result.answers[normalise_key(str(raw_key))] = value
            column = self.column_for(str(raw_key))
            if column and column not in result.columns:
                result.columns[column] = (
                    parse_yes_no(str(value)) if column == "has_family" else value
                )
        return result
