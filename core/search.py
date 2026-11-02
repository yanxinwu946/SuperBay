"""Dependency-free, bounded search ranking shared by both launcher surfaces."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from core.models import Entry


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return text if value == text else value


def score(entry: Entry, query: str, note: str = "") -> int | None:
    """All tokens must match; names rank above tags, paths and note content.

    Short Latin abbreviations can match a name's subsequence (e.g. fl → Flow-Launcher).
    Long prose is only searched literally to keep work bounded and predictable.
    """
    return SearchRecord.build(entry, note).score(tuple(normalize(query).split()))


@dataclass(frozen=True, slots=True)
class SearchRecord:
    name: str
    tags: str
    context: str
    note: str
    initials: str

    @classmethod
    def build(cls, entry: Entry, note: str = ""):
        name = normalize(entry.name)
        return cls(name, normalize(" ".join(entry.tags)),
                   normalize(f"{entry.cat_name} {entry.path} {entry.desc}"), normalize(note),
                   "".join(word[0] for word in re.findall(r"[a-z0-9]+", name)))

    def score(self, tokens: tuple[str, ...]) -> int | None:
        if not tokens:
            return 0
        total = 0
        name, tags, context, note, initials = self.name, self.tags, self.context, self.note, self.initials
        for token in tokens:
            if token == name:
                total += 120
            elif name.startswith(token):
                total += 100
            elif token in name:
                total += 80
            elif token in tags:
                total += 65
            elif token in context:
                total += 40
            elif token in note:
                total += 15
            elif 2 <= len(token) <= 12 and token.isascii() and token.isalnum():
                chars = iter(name)
                if token == initials:
                    total += 70
                elif all(char in chars for char in token):
                    total += 25
                else:
                    return None
            else:
                return None
        return total


class SearchIndex:
    """One record per entry; metadata changes replace only the affected record."""

    def __init__(self):
        self.entries = None
        self.records: dict[int, SearchRecord] = {}

    def reset(self, entries: list[Entry], notes: dict[str, str]):
        self.entries = entries
        self.records = {id(e): SearchRecord.build(e, notes.get(str(e.path), "")) for e in entries}

    def ensure(self, entries: list[Entry], notes: dict[str, str]):
        if entries is not self.entries:
            self.reset(entries, notes)

    def update(self, entry: Entry, note: str = ""):
        self.records[id(entry)] = SearchRecord.build(entry, note)

    def ranked(self, query: str, entries: list[Entry] | None = None):
        tokens = tuple(normalize(query).split())
        source = self.entries if entries is None else entries
        if not tokens:
            return [(entry, 0) for entry in source or ()]
        result = []
        for entry in source or ():
            record = self.records[id(entry)]
            rank = record.score(tokens)
            if rank is not None:
                result.append((entry, rank))
        return result
