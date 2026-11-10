import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

COMMENT = re.compile(r''' "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*' | (?P<comment>\#.*) ''', re.X)
LABEL = re.compile(r"^(?:[\w.$]+\s*[:>]\s*)+")
DATA_DIRECTIVES = {"asect", "rsect", "tplate", "ds", "dc", "align", "ext", "end", "mend", "macro"}
COVERAGE_COUNTS = {
    "file_percent": ("tested_files", "files"),
    "line_percent": ("executed_lines", "mapped_lines"),
    "comment_percent": ("commented_lines", "nonblank_lines"),
}


@dataclass(frozen=True)
class SourceMetrics:
    lines: int
    blank: int
    code: int
    comment_only: int
    inline_comments: int


def source_metrics(path: Path) -> tuple[SourceMetrics, set[int]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    blank = code = comment_only = inline = 0
    instruction_lines = set()
    for line_number, line in enumerate(lines, 1):
        comment = next((match for match in COMMENT.finditer(line) if match.lastgroup == "comment"), None)
        statement = (line[:comment.start()] if comment else line).strip()
        if statement:
            code += 1
            inline += comment is not None
            instruction = LABEL.sub("", statement).split()
            if instruction and instruction[0].lower().rstrip(".") not in DATA_DIRECTIVES:
                instruction_lines.add(line_number)
        elif comment:
            comment_only += 1
        else:
            blank += 1
    return SourceMetrics(len(lines), blank, code, comment_only, inline), instruction_lines


def percent(part: int, total: int) -> float | None:
    return round(100 * part / total, 2) if total else None


class Coverage:
    def __init__(self):
        self.mapped: dict[Path, set[int]] = defaultdict(set)
        self.executed: dict[Path, set[int]] = defaultdict(set)

    def register(self, locations: dict[int, tuple[Path, int]]) -> None:
        for path, line in locations.values():
            self.mapped[path].add(line)

    def record(self, locations: dict[int, tuple[Path, int]], visited: set[int]) -> None:
        for address in locations.keys() & visited:
            path, line = locations[address]
            self.executed[path].add(line)

    def file_report(self, path: Path) -> dict:
        metrics, instruction_lines = source_metrics(path)
        mapped = self.mapped.get(path, set()) & instruction_lines
        executed = self.executed.get(path, set()) & mapped
        return {
            **asdict(metrics),
            "comment_percent": percent(metrics.comment_only + metrics.inline_comments, metrics.lines - metrics.blank),
            "mapped_lines": len(mapped),
            "executed_lines": len(executed),
            "unexecuted_lines": sorted(mapped - executed),
            "line_percent": percent(len(executed), len(mapped)),
        }


def totals(files: list[dict]) -> dict:
    executed = sum(file["executed_lines"] for file in files)
    mapped = sum(file["mapped_lines"] for file in files)
    tested = sum(file["executed_lines"] > 0 for file in files)
    nonblank = sum(file["lines"] - file["blank"] for file in files)
    commented = sum(file["comment_only"] + file["inline_comments"] for file in files)
    counts = {
        "files": len(files),
        "tested_files": tested,
        "mapped_files": sum(file["mapped_lines"] > 0 for file in files),
        "mapped_lines": mapped,
        "executed_lines": executed,
        "commented_lines": commented,
        "nonblank_lines": nonblank,
    }
    return {
        **counts,
        **{metric: percent(counts[part], counts[total]) for metric, (part, total) in COVERAGE_COUNTS.items()},
    }
