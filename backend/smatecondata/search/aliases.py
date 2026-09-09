"""Multilingual concept alias store (spec 0B.1).

Loads ``concepts.yaml`` and builds a lookup that resolves an Arabic, French or
English phrase onto an internal concept key. Every alias is indexed under all
three normalisation schemes, so a French alias is still reachable from an
English-normalised query token and vice versa.

The concept key is a *discovery* handle. Provider metadata returned to the user
keeps its official title, code, unit and definition unchanged.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..core.models import Language
from .normalize import normalize, normalize_all, tokenize

CONCEPTS_FILE = Path(__file__).with_name("concepts.yaml")


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    topic: str | None = None
    ambiguous: bool = False
    distinctions: tuple[str, ...] = ()
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def display(self, language: Language) -> str:
        """Localised label when one exists, else the canonical English label."""
        values = self.aliases.get(language.value) or ()
        return values[0] if values else self.label

    def all_aliases(self) -> list[str]:
        out: list[str] = [self.label]
        for values in self.aliases.values():
            out.extend(values)
        return out


class ConceptStore:
    """Alias -> concept resolution across Arabic, English and French."""

    def __init__(self, concepts: list[Concept]):
        self.concepts: dict[str, Concept] = {c.key: c for c in concepts}
        # normalised alias -> set of concept keys (an alias may be shared)
        self._exact: dict[str, set[str]] = {}
        # single normalised token -> set of concept keys
        self._token: dict[str, set[str]] = {}

        # (normalised alias, concept key, weight), longest alias first so the
        # most specific phrase claims its span before shorter ones are tried.
        self._alias_index: list[tuple[str, str, int]] = []

        for concept in concepts:
            for alias in concept.all_aliases():
                for form in normalize_all(alias):
                    if not form:
                        continue
                    self._exact.setdefault(form, set()).add(concept.key)
                    self._alias_index.append(
                        (form, concept.key, len(form.split()) * 10 + len(form))
                    )
                    for token in form.split():
                        if len(token) >= 3 or not token.isascii():
                            self._token.setdefault(token, set()).add(concept.key)

        self._alias_index.sort(key=lambda t: (len(t[0].split()), len(t[0])),
                               reverse=True)

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "ConceptStore":
        raw = yaml.safe_load((path or CONCEPTS_FILE).read_text(encoding="utf-8"))
        concepts = [
            Concept(
                key=item["key"],
                label=item["label"],
                topic=item.get("topic"),
                ambiguous=bool(item.get("ambiguous", False)),
                distinctions=tuple(item.get("distinctions", []) or ()),
                aliases={
                    lang: tuple(values)
                    for lang, values in (item.get("aliases") or {}).items()
                },
            )
            for item in (raw.get("concepts") or [])
        ]
        return cls(concepts)

    # -- lookup ----------------------------------------------------------

    def get(self, key: str) -> Concept | None:
        return self.concepts.get(key)

    def resolve_phrase(self, phrase: str) -> list[Concept]:
        """Concepts whose alias matches ``phrase`` exactly (after normalising)."""
        keys: set[str] = set()
        for form in normalize_all(phrase):
            keys |= self._exact.get(form, set())
        return [self.concepts[k] for k in sorted(keys)]

    def find_in_query(self, query: str,
                      language: Language | None = None) -> list[Concept]:
        """Concepts mentioned anywhere in a free-form query.

        Matching walks longest-alias-first and *consumes* each matched span, so
        ``GDP per capita`` yields only ``gdp_per_capita`` -- the bare ``GDP``
        alias cannot re-match text already claimed by the longer phrase. Without
        this, a five-indicator request silently grows extra columns.
        """
        normalised = normalize(query, language)
        haystack = f" {normalised} "
        found: list[tuple[str, int]] = []
        seen: set[str] = set()

        for form, key, weight in self._alias_index:
            needle = f" {form} "
            if needle not in haystack:
                continue
            haystack = haystack.replace(needle, " \x00 ")
            if key not in seen:
                seen.add(key)
                found.append((key, weight))

        # Token fallback: catches a query whose only signal is one alias word.
        if not found:
            for token in tokenize(query, language):
                for key in self._token.get(token, set()):
                    if key not in seen:
                        seen.add(key)
                        found.append((key, 1))

        found.sort(key=lambda kv: kv[1], reverse=True)
        return [self.concepts[k] for k, _ in found]

    @staticmethod
    def _contains_phrase(haystack: str, needle: str) -> bool:
        """Whole-token containment, so ``m2`` never matches inside ``m20``."""
        h = f" {haystack} "
        n = f" {needle} "
        return n in h

    @property
    def ambiguous_keys(self) -> set[str]:
        return {k for k, c in self.concepts.items() if c.ambiguous}


@functools.lru_cache(maxsize=1)
def get_concept_store() -> ConceptStore:
    return ConceptStore.from_yaml()
