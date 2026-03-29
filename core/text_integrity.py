from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import codecs
import re


TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".bat",
        ".cmd",
        ".css",
        ".html",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".ps1",
        ".py",
        ".sh",
        ".sql",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

_CP1252_MOJIBAKE = ("\u00C3", "\u00C2", "\u00E2\u20AC")
_QUESTION_MARK_BEFORE_HANGUL = re.compile(r"\?[\uAC00-\uD7A3\u3131-\u314E\u314F-\u3163]")


@dataclass(frozen=True)
class TextFileFormat:
    has_utf8_bom: bool
    newline: str


@dataclass(frozen=True)
class TextFileSnapshot:
    text: str
    format: TextFileFormat
    suspicious_markers: tuple[str, ...]


def is_supported_text_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TEXT_EXTENSIONS


def detect_newline_style(data: bytes) -> str:
    crlf_count = data.count(b"\r\n")
    normalized = data.replace(b"\r\n", b"")
    cr_count = normalized.count(b"\r")
    lf_count = normalized.count(b"\n")

    if crlf_count and not cr_count and not lf_count:
        return "crlf"
    if lf_count and not crlf_count and not cr_count:
        return "lf"
    if not crlf_count and not cr_count and not lf_count:
        return "none"
    return "mixed"


def decode_utf8_bytes(data: bytes) -> tuple[str, TextFileFormat]:
    has_bom = data.startswith(codecs.BOM_UTF8)
    body = data[len(codecs.BOM_UTF8) :] if has_bom else data
    text = body.decode("utf-8")
    return text, TextFileFormat(
        has_utf8_bom=has_bom,
        newline=detect_newline_style(body),
    )


def inspect_text_bytes(data: bytes) -> TextFileSnapshot:
    text, file_format = decode_utf8_bytes(data)
    markers = tuple(sorted(set(find_suspicious_markers(text))))
    return TextFileSnapshot(text=text, format=file_format, suspicious_markers=markers)


def inspect_text_file(path: str | Path) -> TextFileSnapshot:
    return inspect_text_bytes(Path(path).read_bytes())


def normalize_newlines(text: str, newline: str) -> str:
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if newline == "crlf":
        return normalized.replace("\n", "\r\n")
    return normalized


def encode_text(text: str, file_format: TextFileFormat) -> bytes:
    body = normalize_newlines(text, file_format.newline).encode("utf-8")
    if file_format.has_utf8_bom:
        return codecs.BOM_UTF8 + body
    return body


def write_text_preserving_format(
    path: str | Path,
    text: str,
    *,
    default_has_utf8_bom: bool = False,
    default_newline: str = "lf",
) -> TextFileFormat:
    target = Path(path)
    if target.exists():
        file_format = inspect_text_file(target).format
    else:
        file_format = TextFileFormat(
            has_utf8_bom=default_has_utf8_bom,
            newline=default_newline,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encode_text(text, file_format))
    return file_format


def find_suspicious_markers(text: str) -> list[str]:
    markers: list[str] = []
    sample = str(text or "")
    if "\ufffd" in sample:
        markers.append("replacement_character")
    if _QUESTION_MARK_BEFORE_HANGUL.search(sample):
        markers.append("question_mark_before_hangul")
    if any(token in sample for token in _CP1252_MOJIBAKE):
        markers.append("cp1252_utf8_mojibake")
    return markers