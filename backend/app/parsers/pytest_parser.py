"""Python / pytest test discovery via AST.

Repository files are untrusted input, so this parser **never imports or executes
the code it reads**, it only walks the syntax tree. A file with a syntax error
is reported and skipped.

Extracted per test:
  name, file, line number, source body, docstring, pytest markers,
  `@pytest.mark.parametrize` values, assertion expressions and literals.

From those it infers feature / scenario / inputs / expected behaviour using the
same `intelligence.extraction` rules that incidents go through.
"""
from __future__ import annotations

import ast
import contextlib
from pathlib import Path

from ..config.logging_conf import get_logger
from ..domain.models import NormalizedTest
from ..intelligence.extraction import (
    detect_signals,
    extract_conditions,
    humanize_test_name,
    infer_feature,
    merge_conditions,
)
from .base import ParseOutcome, TestParser

log = get_logger(__name__)

_MAX_CODE_CHARS = 4000


class PytestParser(TestParser):
    framework = "pytest"

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() != ".py":
            return False
        name = path.name.lower()
        return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"

    def parse(self, path: Path) -> ParseOutcome:
        outcome = ParseOutcome()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            outcome.warn(str(path), f"Could not read file: {exc}", "ERROR")
            outcome.files_skipped = 1
            return outcome

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            outcome.warn(str(path), f"Syntax error at line {exc.lineno}; file skipped.", "ERROR")
            outcome.files_skipped = 1
            return outcome
        except (ValueError, RecursionError) as exc:
            outcome.warn(str(path), f"Could not parse: {exc}", "ERROR")
            outcome.files_skipped = 1
            return outcome

        outcome.files_scanned = 1
        source_lines = source.splitlines()
        module_feature = infer_feature(path.stem, default="")

        for node, class_name in self._iter_test_functions(tree):
            try:
                test = self._build_test(node, class_name, path, source_lines, module_feature)
            except Exception as exc:  # one odd function must not kill the file
                outcome.warn(f"{path.name}:{getattr(node, 'lineno', '?')}", f"Test skipped: {exc}")
                continue
            outcome.tests.append(test)

        return outcome

    # -- traversal ----------------------------------------------------------

    def _iter_test_functions(self, tree: ast.Module):
        """Yield (function_node, enclosing_class_name) for every pytest test."""
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                yield node, None
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                        yield child, node.name

    # -- extraction ---------------------------------------------------------

    def _build_test(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_name: str | None,
        path: Path,
        source_lines: list[str],
        module_feature: str,
    ) -> NormalizedTest:
        docstring = ast.get_docstring(node) or ""
        markers, parametrized = self._decorators(node)
        assertions, literals = self._assertions_and_literals(node)

        end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
        code = "\n".join(source_lines[node.lineno - 1 : end_line])[:_MAX_CODE_CHARS]

        readable = humanize_test_name(node.name)
        feature = (
            infer_feature(node.name, docstring, class_name or "", default="")
            or module_feature
            or infer_feature(str(path.parent), default="Unknown")
        )

        # Conditions, in descending order of trust: values the test literally
        # passes in, parametrize values, values it asserts on, then whatever the
        # name and docstring imply.
        inputs = merge_conditions(
            self._call_keyword_inputs(node),
            parametrized,
            extract_conditions(readable),
            extract_conditions(docstring),
        )

        expected = self._expected_behavior(docstring, assertions)
        qualified = f"{class_name}::{node.name}" if class_name else node.name
        test_id = f"{path.stem}::{qualified}"

        return NormalizedTest(
            id=test_id,
            name=node.name,
            feature=feature or "Unknown",
            scenario=docstring.strip().splitlines()[0].strip() if docstring.strip() else readable,
            inputs=inputs,
            expected_behavior=expected,
            tags=markers,
            source=str(path),
            framework=self.framework,
            line_number=node.lineno,
            code=code,
            assertions=assertions,
            literals=literals,
            extra={
                "class": class_name,
                "docstring": docstring[:500],
                "signals": detect_signals(readable, docstring, " ".join(assertions)),
            },
        )

    def _decorators(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> tuple[list[str], dict[str, str]]:
        """Return (marker names, values taken from @pytest.mark.parametrize)."""
        markers: list[str] = []
        parametrized: dict[str, str] = {}

        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call else decorator
            name = self._dotted_name(target)
            if not name:
                continue
            marker = name.split(".")[-1]
            if "mark" in name:
                markers.append(marker)

            if marker == "parametrize" and call and call.args:
                parametrized.update(self._parametrize_values(call))

        return markers, parametrized

    def _parametrize_values(self, call: ast.Call) -> dict[str, str]:
        """Turn `parametrize("discount", [0, 10, 100])` into {"discount": "0, 10, 100"}."""
        names_node = call.args[0]
        raw_names = self._literal(names_node)
        if not isinstance(raw_names, str):
            return {}
        names = [n.strip() for n in raw_names.replace(",", " ").split() if n.strip()]
        if not names or len(call.args) < 2:
            return {}

        try:
            values = ast.literal_eval(call.args[1])
        except (ValueError, SyntaxError, TypeError):
            return {}
        if not isinstance(values, (list, tuple)):
            return {}

        columns: dict[str, list[str]] = {name: [] for name in names}
        for row in values:
            cells = row if isinstance(row, (list, tuple)) and len(names) > 1 else (row,)
            # A malformed parametrize row may not supply every name; take what
            # it does supply rather than discarding the whole decorator.
            for name, cell in zip(names, cells, strict=False):
                columns[name].append(_render(cell))

        return {
            name: ", ".join(dict.fromkeys(values_list))
            for name, values_list in columns.items()
            if values_list
        }

    def _call_keyword_inputs(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> dict[str, str]:
        """Inputs the test literally passes in: `checkout(discount="100%")`.

        This is the most reliable input signal available from source, because it
        is the data the test actually drives the system with rather than a
        description of it.
        """
        inputs: dict[str, str] = {}
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            for keyword in child.keywords:
                if keyword.arg is None or keyword.arg in inputs:
                    continue  # **kwargs, or already captured
                value = self._literal(keyword.value)
                if value is None and not isinstance(keyword.value, ast.Constant):
                    continue
                rendered = _render(value)
                if rendered and len(rendered) <= 60:
                    inputs[keyword.arg] = rendered
        return inputs

    def _assertions_and_literals(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> tuple[list[str], list[str]]:
        assertions: list[str] = []
        literals: list[str] = []

        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                # An assertion we cannot render is not worth failing the file for.
                with contextlib.suppress(Exception):
                    assertions.append(ast.unparse(child.test)[:200])
            elif isinstance(child, ast.Constant) and not isinstance(child.value, bool):
                if isinstance(child.value, (str, int, float)):
                    rendered = _render(child.value)
                    if rendered and len(rendered) <= 60:
                        literals.append(rendered)
            elif isinstance(child, ast.Call):
                # pytest.raises(ValueError) is an assertion about behaviour.
                name = self._dotted_name(child.func) or ""
                if name.endswith("raises") and child.args:
                    target = self._dotted_name(child.args[0]) or "Exception"
                    assertions.append(f"raises {target}")

        return assertions[:25], list(dict.fromkeys(literals))[:40]

    def _expected_behavior(self, docstring: str, assertions: list[str]) -> str:
        """Prefer an explicit docstring statement, else summarise the assertions."""
        for line in docstring.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith(("expected:", "expect:", "then:", "should ", "asserts ")):
                return stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped
        if docstring.strip():
            lines = [line.strip() for line in docstring.strip().splitlines() if line.strip()]
            if len(lines) > 1:
                return lines[-1]
        return "; ".join(assertions[:3])

    def _dotted_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._dotted_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    def _literal(self, node: ast.AST):
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError, TypeError):
            return None


def _render(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    if isinstance(value, str) and value == "":
        return "empty"
    return str(value)
