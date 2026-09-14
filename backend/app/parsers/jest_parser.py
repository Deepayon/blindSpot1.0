"""JavaScript / TypeScript (Jest, Vitest) test discovery.

Intentionally lightweight: a regex + brace-matching scan rather than a real JS
parser. That is enough to recover `describe`/`it`/`test` titles and `expect`
assertions, which is all the normalised model needs. Anything more would mean
shipping a JS parser for a POC that already declares pytest as its primary
target.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..domain.models import NormalizedTest
from ..intelligence.extraction import (
    derive_feature,
    detect_signals,
    extract_conditions,
    merge_conditions,
)
from .base import ParseOutcome, TestParser

_TEST_RE = re.compile(
    r"""\b(?P<kind>it|test)(?:\.(?:each|only|skip|concurrent))?\s*\(\s*
        (?P<quote>['"`])(?P<title>(?:\\.|(?!(?P=quote)).)*)(?P=quote)""",
    re.VERBOSE,
)
_DESCRIBE_RE = re.compile(
    r"""\bdescribe(?:\.(?:each|only|skip))?\s*\(\s*
        (?P<quote>['"`])(?P<title>(?:\\.|(?!(?P=quote)).)*)(?P=quote)""",
    re.VERBOSE,
)
_EXPECT_RE = re.compile(r"expect\(([^)]{0,120})\)\s*\.\s*([A-Za-z.]+)\(([^)]{0,120})\)")
_SKIP_RE = re.compile(r"\b(it|test|describe)\.skip\b")

_MAX_BODY_CHARS = 4000


class JestTestParser(TestParser):
    framework = "jest"

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            return False
        name = path.name.lower()
        return ".test." in name or ".spec." in name

    def parse(self, path: Path) -> ParseOutcome:
        outcome = ParseOutcome()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            outcome.warn(str(path), f"Could not read file: {exc}", "ERROR")
            outcome.files_skipped = 1
            return outcome

        outcome.files_scanned = 1
        suites = [(m.start(), m.group("title")) for m in _DESCRIBE_RE.finditer(source)]
        # `.test.`/`.spec.` suffixes are stripped so `checkout.test.ts` reads as
        # "Checkout" rather than "Checkout Test".
        module_feature = derive_feature(
            str(path).replace(".test.", ".").replace(".spec.", "."), default="Unknown"
        )

        for index, match in enumerate(_TEST_RE.finditer(source), start=1):
            title = match.group("title").strip()
            if not title:
                continue
            suite = self._enclosing_suite(suites, match.start())
            body = self._body(source, match.end())
            assertions = [
                f"expect({target}).{matcher}({expected})".strip()
                for target, matcher, expected in _EXPECT_RE.findall(body)
            ][:25]

            line_number = source.count("\n", 0, match.start()) + 1
            # A describe() block names the area under test, so it is a better
            # label than the file when present.
            feature = derive_feature(suite, default="") or module_feature or "Unknown"
            skipped = bool(_SKIP_RE.search(source[max(0, match.start() - 20) : match.end()]))

            outcome.tests.append(
                NormalizedTest(
                    id=f"{path.stem}::{suite}::{title}" if suite else f"{path.stem}::{title}",
                    name=title,
                    feature=feature,
                    scenario=f"{suite}, {title}" if suite else title,
                    inputs=merge_conditions(
                        extract_conditions(title),
                        extract_conditions(suite),
                        extract_conditions(" ".join(assertions)),
                    ),
                    expected_behavior="; ".join(assertions[:3]),
                    tags=["skipped"] if skipped else [],
                    source=str(path),
                    framework=self.framework,
                    line_number=line_number,
                    code=body[:_MAX_BODY_CHARS],
                    assertions=assertions,
                    extra={
                        "suite": suite,
                        "index": index,
                        "skipped": skipped,
                        "signals": detect_signals(title, suite, " ".join(assertions)),
                    },
                )
            )

        return outcome

    def _enclosing_suite(self, suites: list[tuple[int, str]], position: int) -> str:
        """Nearest preceding `describe` title."""
        title = ""
        for start, name in suites:
            if start < position:
                title = name
            else:
                break
        return title.strip()

    def _body(self, source: str, start: int) -> str:
        """Return the callback body by matching braces from the first `{`."""
        opening = source.find("{", start)
        if opening == -1:
            return ""
        depth = 0
        for index in range(opening, min(len(source), opening + 20_000)):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[opening : index + 1]
        return source[opening : opening + _MAX_BODY_CHARS]
