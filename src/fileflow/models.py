from __future__ import annotations

import hashlib
import json
import string
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from fileflow.errors import FileFlowError
from fileflow.safety import Identity, component, relative

DEFAULT_CATEGORIES = (
    ("Documents", (".pdf", ".txt", ".md", ".csv", ".docx", ".xlsx", ".odt")),
    ("Images", (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")),
    ("Archives", (".zip", ".tar", ".gz", ".7z")),
    ("Media", (".mp4", ".mov", ".mp3", ".wav", ".flac")),
    ("Code", (".py", ".js", ".ts", ".cpp", ".c", ".json", ".html")),
)


@dataclass(frozen=True)
class Settings:
    group_by: str = "type"
    destination: str = "Organized"
    rename_template: str | None = None
    recursive: bool = False
    include_hidden: bool = False
    categories: tuple = DEFAULT_CATEGORIES

    def __post_init__(self) -> None:
        if self.group_by not in {"type", "date", "none"}:
            raise FileFlowError("Group by type, date, or none")
        component(self.destination)
        if self.destination.casefold() == ".fileflow":
            raise FileFlowError("Destination is reserved for the journal")
        if type(self.recursive) is not bool or type(self.include_hidden) is not bool:
            raise FileFlowError("recursive/include_hidden must be booleans")
        seen_names, seen_ext = set(), set()
        for name, extensions in self.categories:
            component(name)
            if name.casefold() == ".fileflow" or name.casefold() in seen_names:
                raise FileFlowError("Category names must be unique and not reserved")
            seen_names.add(name.casefold())
            for extension in extensions:
                if (
                    not isinstance(extension, str)
                    or not extension.startswith(".")
                    or len(extension) < 2
                ):
                    raise FileFlowError("Category extensions must start with a dot")
                component("x" + extension)
                if extension.casefold() in seen_ext:
                    raise FileFlowError(f"Duplicate extension rule: {extension}")
                seen_ext.add(extension.casefold())
        if self.rename_template is not None:
            if not self.rename_template or len(self.rename_template) > 240:
                raise FileFlowError("Provide a nonempty rename template up to 240 characters")
            try:
                for _, name, spec, conversion in string.Formatter().parse(self.rename_template):
                    if name is not None and (
                        name not in {"stem", "suffix", "index", "date"} or conversion
                    ):
                        raise ValueError("Unsupported field or conversion")
                    if spec and (
                        name != "index" or spec not in {"d", "02d", "03d", "04d", "05d", "06d"}
                    ):
                        raise ValueError("Only index supports numeric formatting, e.g. {index:03d}")
                component(
                    self.rename_template.format(
                        stem="example", suffix=".txt", index=1, date="2026-01-01"
                    )
                )
            except (ValueError, KeyError, IndexError) as exc:
                raise FileFlowError(f"Invalid rename template: {exc}") from exc

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Settings:
        try:
            value = dict(value)
            if "categories" in value:
                value["categories"] = tuple((name, tuple(ext)) for name, ext in value["categories"])
            return cls(**value)
        except (TypeError, ValueError, KeyError) as exc:
            raise FileFlowError(f"Invalid settings: {exc}") from exc


@dataclass(frozen=True)
class Move:
    source: str
    destination: str
    identity: Identity


@dataclass(frozen=True)
class Plan:
    root: str
    settings: Settings
    moves: tuple[Move, ...]
    skipped: tuple[tuple[str, str], ...]
    created_at: str
    version: int = 1

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["settings"] = self.settings.to_dict()
        return payload

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def save(self, destination: Path) -> None:
        with destination.open("x", encoding="utf-8") as stream:
            json.dump({**self.to_dict(), "digest": self.digest}, stream, indent=2)
            stream.write("\n")

    @classmethod
    def load(cls, path: Path) -> Plan:
        try:
            if path.stat().st_size > 20 * 1024 * 1024:
                raise FileFlowError("Plan exceeds the 20 MiB input limit")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw["version"] != 1:
                raise FileFlowError("Unsupported plan version")
            if not isinstance(raw["root"], str) or not Path(raw["root"]).is_absolute():
                raise FileFlowError("Plan root must be an absolute path")
            moves = []
            sources, destinations = set(), set()
            for item in raw["moves"]:
                src, dst = item["source"], item["destination"]
                relative(src)
                relative(dst)
                if src == dst or src.casefold() in sources or dst.casefold() in destinations:
                    raise FileFlowError("Duplicate or invalid plan paths")
                sources.add(src.casefold())
                destinations.add(dst.casefold())
                identity = Identity(**item["identity"])
                if any(
                    type(n) is not int or n < 0
                    for n in (identity.size, identity.device, identity.inode)
                ):
                    raise FileFlowError("Invalid file identity")
                if len(identity.sha256) != 64 or any(
                    c not in "0123456789abcdef" for c in identity.sha256
                ):
                    raise FileFlowError("Invalid SHA-256 fingerprint")
                moves.append(Move(src, dst, identity))
            if sources & destinations:
                raise FileFlowError("Rename chains/cycles are not accepted")
            plan = cls(
                raw["root"],
                Settings.from_dict(raw["settings"]),
                tuple(moves),
                tuple(tuple(s) for s in raw["skipped"]),
                raw["created_at"],
            )
            if raw["digest"] != plan.digest:
                raise FileFlowError("Plan checksum does not match; create a fresh preview")
            return plan
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise FileFlowError(f"Cannot load plan: {exc}") from exc


def now() -> str:
    return datetime.now(UTC).isoformat()
