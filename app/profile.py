"""Dataset profile: the data contract that drives validation, cleaning and the dashboard.

Adding support for a new kind of operational dataset means adding a JSON profile,
not rewriting the pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "profiles"
VALID_TYPES = {"string", "integer", "decimal", "date"}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    required: bool = False
    allowed: tuple[str, ...] | None = None
    synonyms: dict[str, str] = field(default_factory=dict)
    case: str | None = None          # "title" | "upper" | None
    pattern: str | None = None
    pattern_hint: str | None = None
    min: float | str | None = None
    max: float | None = None
    formats: tuple[str, ...] = ()
    not_future: bool = False
    fill: str | None = None          # value used when an optional field is missing
    # optional field that SHOULD be present when another column has certain values
    expected_when: dict | None = None


@dataclass(frozen=True)
class DatasetProfile:
    name: str
    title: str
    description: str
    key: str
    roles: dict[str, str]
    sla_days: int
    columns: tuple[ColumnSpec, ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def required_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.required]

    def column(self, name: str) -> ColumnSpec:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


def _parse(raw: dict) -> DatasetProfile:
    cols = []
    for c in raw["columns"]:
        if c["type"] not in VALID_TYPES:
            raise ValueError(f"Unknown column type {c['type']!r} for {c['name']}")
        cols.append(ColumnSpec(
            name=c["name"], type=c["type"], required=c.get("required", False),
            allowed=tuple(c["allowed"]) if "allowed" in c else None,
            synonyms=c.get("synonyms", {}), case=c.get("case"), pattern=c.get("pattern"), pattern_hint=c.get("pattern_hint"),
            min=c.get("min"), max=c.get("max"), formats=tuple(c.get("formats", ())),
            not_future=c.get("not_future", False), fill=c.get("fill"),
            expected_when=c.get("expected_when"),
        ))
    profile = DatasetProfile(
        name=raw["name"], title=raw["title"], description=raw.get("description", ""),
        key=raw["key"], roles=raw.get("roles", {}), sla_days=raw.get("sla_days", 5),
        columns=tuple(cols),
    )
    names = set(profile.column_names)
    if profile.key not in names:
        raise ValueError("Profile key column is not defined in columns")
    missing_roles = [v for v in profile.roles.values() if v not in names]
    if missing_roles:
        raise ValueError(f"Profile roles reference unknown columns: {missing_roles}")
    return profile


@lru_cache
def load_profile(name: str) -> DatasetProfile:
    path = PROFILE_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Dataset profile not found: {name}")
    return _parse(json.loads(path.read_text(encoding="utf-8")))
