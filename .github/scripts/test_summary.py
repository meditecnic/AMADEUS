"""Publish pytest or Vitest JUnit results as GitHub Actions annotations."""

import argparse
from collections import Counter
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def shorten(message, limit):
    """Bound the UTF-8 message size after workflow-command escaping."""
    if len(escape(message).encode("utf-8")) <= limit:
        return message
    prefix = []
    remaining = limit - 3
    for char in message:
        size = len(escape(char).encode("utf-8"))
        if size > remaining:
            break
        prefix.append(char)
        remaining -= size
    return "".join(prefix) + "..."


def escape(value, *, property_value=False):
    value = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def annotation(level, title, message):
    message = escape(shorten(message, 60_000))
    return f"::{level} title={escape(title, property_value=True)}::{message}"


def case_list(cases):
    # Fifty lines of at most 1,000 escaped bytes leave room for newlines and
    # the omitted-count line within the 60 KB annotation message budget.
    lines = [shorten(case, 1_000) for case in cases[:50]]
    if len(cases) > 50:
        lines.append(f"还有 {len(cases) - 50} 个")
    return "\n".join(lines)


def publish(lines):
    for line in lines:
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with Path(summary).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("```text\n" + "\n".join(lines) + "\n```\n")
        except OSError as exc:
            print(annotation("warning", "Test summary", f"Cannot write step summary: {exc}"))


def elapsed(element):
    # Prefer wall time over summing parallel test durations; never count both
    # a testsuites wrapper and its child suites.
    if "time" in element.attrib:
        return float(element.get("time"))
    return sum(elapsed(child) for child in element if child.tag in {"testsuite", "testcase"})


def test_name(case, suite):
    filename = case.get("file") or suite.get("file")
    name = case.get("name", "(unnamed test)")
    classname = case.get("classname", "")
    if not filename:
        # Pytest's default xunit2 format omits file, but includes its module
        # and optional test class in classname. Vitest uses the file path.
        parts = classname.split(".")
        modules = [i for i, part in enumerate(parts) if part.startswith("test_")]
        if modules and "/" not in classname and "\\" not in classname:
            end = modules[-1] + 1
            filename = "/".join(parts[:end]) + ".py"
            name = "::".join([*parts[end:], name])
        else:
            filename = classname or suite.get("name", "(unknown file)")
    return f"{filename}::{name}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--level", choices=("notice", "warning"), default="notice")
    parser.add_argument("--outcome", choices=("success", "failure", "cancelled", "skipped"))
    args = parser.parse_args()
    # Windows runner Python may otherwise encode redirected stdout as cp1252.
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        root = ET.parse(args.junit).getroot()
        duration = elapsed(root)
    except (OSError, ET.ParseError, ValueError) as exc:
        publish([annotation("error", args.label, f"Cannot read JUnit XML {args.junit}: {exc}")])
        return 1

    severity = {"passed": 0, "skipped": 1, "xfailed": 2, "failed": 3, "errors": 4}
    results = {}
    parents = {child: parent for parent in root.iter() for child in parent}
    for case in root.iter("testcase"):
        key = tuple(case.get(attribute, "") for attribute in ("file", "classname", "name"))
        name = test_name(case, parents.get(case, root))
        skip = case.find("skipped")
        reason = ""
        if case.find("error") is not None:
            status = "errors"
        elif case.find("failure") is not None:
            status = "failed"
        elif skip is not None and skip.get("type") == "pytest.xfail":
            status = "xfailed"
        elif skip is not None:
            status = "skipped"
            reason = skip.get("message") or (skip.text or "").strip() or "(no reason)"
        else:
            status = "passed"
        if key not in results or severity[status] > severity[results[key][0]]:
            results[key] = (status, name, reason)

    counts = Counter(status for status, _, _ in results.values())
    counts["total"] = len(results)
    failed = [name for status, name, _ in results.values() if status in {"failed", "errors"}]
    skipped = [
        f"{name} — {reason}"
        for status, name, reason in results.values()
        if status == "skipped"
    ]

    summary = " · ".join(
        f"{key} {counts[key]}"
        for key in ("total", "passed", "failed", "errors", "skipped", "xfailed")
    )
    lines = [annotation(args.level, args.label, f"{summary} · time {duration:.3f}s")]
    failure_level = "warning" if args.level == "warning" else "error"
    for level, title, cases in (
        (failure_level, f"{args.label} failed", failed),
        ("notice", f"{args.label} skipped", skipped),
    ):
        if cases:
            lines.append(annotation(level, title, case_list(cases)))
    if args.outcome == "failure" and counts["failed"] == 0 and counts["errors"] == 0:
        lines.append(annotation(
            failure_level,
            f"{args.label} failed",
            "测试步骤失败，但 JUnit 里没有失败的用例（可能是未捕获的错误），请看日志",
        ))
    publish(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
