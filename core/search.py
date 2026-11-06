"""Dependency-free, bounded search ranking shared by both launcher surfaces."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


@lru_cache(maxsize=4096)
def phonetic(text):
    if text.isascii(): return ""
    from pypinyin import lazy_pinyin, Style
    return " ".join(("".join(lazy_pinyin(text)), "".join(lazy_pinyin(text, style=Style.FIRST_LETTER))))

from dataclasses import dataclass

from core import entry_kind
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
    kind: str
    phonetic_name: str
    actual_tags: str
    kind_text: str
    kind_keys: tuple[str, ...]

    @classmethod
    def build(cls, entry: Entry, note: str = ""):
        name = normalize(entry.name)
        aliases = " ".join(entry.aliases)
        kinds = entry_kind.kinds_of(entry)
        return cls(name, normalize(" ".join(entry.tags) + " " + aliases),
                   normalize(f"{entry.cat_name} {entry.path} {entry.desc}"), normalize(note),
                   "".join(word[0] for word in re.findall(r"[a-z0-9]+", name)), entry.entry_type,
                   normalize(phonetic(entry.name + " " + aliases)), normalize(" ".join(entry.tags)),
                   normalize(entry_kind.search_text(entry)), kinds)

    def score(self, tokens: tuple[str, ...]) -> int | None:
        if not tokens:
            return 0
        total = 0
        name, tags, context, note, initials = self.name, self.tags, self.context, self.note, self.initials
        for token in tokens:
            if token.startswith(('语言:', 'lang:')):
                value = token.split(':', 1)[1]
                if not value or (value not in self.kind_keys and value not in self.kind_text):
                    return None
                total += 60
                continue
            if token.startswith(('标签:', 'tag:')):
                if not token.split(':', 1)[1] or token.split(':', 1)[1] not in self.actual_tags: return None
                total += 65
                continue
            if token.startswith(('类型:', 'type:')):
                value = token.split(':', 1)[1]
                value = {'工具': 'tool', '文件': 'file', '目录': 'dir'}.get(value, value)
                if value != self.kind: return None
                continue
            if token == name:
                total += 120
            elif name.startswith(token):
                total += 100
            elif token in name:
                total += 80
            elif token in tags:
                total += 65
            elif token in self.kind_text:
                total += 55
            elif token in context:
                total += 40
            elif token in self.phonetic_name:
                total += 30
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
