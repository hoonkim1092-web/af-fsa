import argparse
from datetime import datetime
from pathlib import Path
import sys


LOG_PATH = Path("PROJECT_LOG.md")


def now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_time() -> str:
    return datetime.now().strftime("%H:%M")


def section_header(date_str: str) -> str:
    return f"## {date_str} Session"


def ensure_log_file(path: Path) -> None:
    if not path.exists():
        path.write_text("# Project Log\n\n", encoding="utf-8")


def ensure_today_section(path: Path, date_str: str) -> str:
    text = path.read_text(encoding="utf-8")
    header = section_header(date_str)
    if header in text:
        return text
    add = (
        f"{header}\n\n"
        "### Timeline\n"
        "- (create first entry with `save` command)\n\n"
    )
    if text.endswith("\n"):
        text += add
    else:
        text += "\n\n" + add
    path.write_text(text, encoding="utf-8")
    return text


def add_timeline_entry(path: Path, date_str: str, entry_type: str, message: str) -> None:
    text = ensure_today_section(path, date_str)
    header = section_header(date_str)
    parts = text.split(header, 1)
    before = parts[0]
    after = parts[1]

    next_section_index = after.find("\n## ")
    if next_section_index == -1:
        current = after
        rest = ""
    else:
        current = after[:next_section_index]
        rest = after[next_section_index:]

    timeline_header = "\n### Timeline\n"
    if timeline_header not in current:
        current += "\n### Timeline\n"
    line = f"- {now_time()} [{entry_type}] {message}\n"
    current += line

    merged = before + header + current + rest
    path.write_text(merged, encoding="utf-8")


def find_section(text: str, date_str: str | None) -> str | None:
    if date_str:
        header = section_header(date_str)
        if header not in text:
            return None
        start = text.index(header)
    else:
        lines = text.splitlines()
        section_lines = [ln for ln in lines if ln.startswith("## ") and not ln.startswith("### ")]
        session_headers = [ln for ln in section_lines if ln.endswith(" Session")]
        if not session_headers:
            return None
        header = session_headers[-1]
        start = text.index(header)
    tail = text[start:]
    next_idx = tail.find("\n## ", 1)
    if next_idx == -1:
        return tail.strip() + "\n"
    return tail[:next_idx].strip() + "\n"


def cmd_save(args: argparse.Namespace) -> int:
    ensure_log_file(LOG_PATH)
    date_str = args.date or now_date()
    add_timeline_entry(LOG_PATH, date_str, args.type.upper(), args.message.strip())
    print(f"Saved entry to {LOG_PATH} ({date_str})")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if not LOG_PATH.exists():
        print(f"{LOG_PATH} not found")
        return 1
    text = LOG_PATH.read_text(encoding="utf-8")
    section = find_section(text, args.date)
    if not section:
        if args.date:
            print(f"No section found for {args.date}")
            return 1
        print("No session section found")
        return 1
    print(section)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Save/read project conversation and progress context."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    save = sub.add_parser("save", help="Append a timeline entry to PROJECT_LOG.md")
    save.add_argument("message", help="Entry text to save")
    save.add_argument("--type", default="NOTE", help="Entry type label (default: NOTE)")
    save.add_argument("--date", help="Date in YYYY-MM-DD (default: today)")
    save.set_defaults(func=cmd_save)

    read = sub.add_parser("read", help="Read latest or date-specific session section")
    read.add_argument("--date", help="Date in YYYY-MM-DD (default: latest section)")
    read.set_defaults(func=cmd_read)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
