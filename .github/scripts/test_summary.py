"""Publish pytest or Vitest JUnit results as GitHub Actions annotations."""

import argparse
from collections import Counter
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def escape(value, *, property_value=False):
    value = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def annotation(level, title, message):
    return f"::{level} title={escape(title, property_value=True)}::{escape(message)}"


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
    args = parser.parse_args()
    # Windows runner Python may otherwise encode redirected stdout as cp1252.
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        root = ET.parse(args.junit).getroot()
        duration = elapsed(root)
    except (OSError, ET.ParseError, ValueError) as exc:
        publish([annotation("error", args.label, f"Cannot read JUnit XML {args.junit}: {exc}")])
        return 1

    counts = Counter()
    failed = []
    skipped = []
    parents = {child: parent for parent in root.iter() for child in parent}
    for case in root.iter("testcase"):
        counts["total"] += 1
        name = test_name(case, parents.get(case, root))
        skip = case.find("skipped")
        if case.find("error") is not None:
            counts["errors"] += 1
            failed.append(name)
        elif case.find("failure") is not None:
            counts["failed"] += 1
            failed.append(name)
        elif skip is not None and skip.get("type") == "pytest.xfail":
            counts["xfailed"] += 1
        elif skip is not None:
            counts["skipped"] += 1
            reason = skip.get("message") or (skip.text or "").strip() or "(no reason)"
            skipped.append(f"{name} — {reason}")
        else:
            counts["passed"] += 1

    summary = " · ".join(
        f"{key} {counts[key]}"
        for key in ("total", "passed", "failed", "errors", "skipped", "xfailed")
    )
    lines = [annotation(args.level, args.label, f"{summary} · time {duration:.3f}s")]
    for level, title, cases, limit in (
        ("error", f"{args.label} failed", failed, 20),
        ("notice", f"{args.label} skipped", skipped, 10),
    ):
        lines.extend(annotation(level, title, case) for case in cases[:limit])
        if len(cases) > limit:
            lines.append(annotation(level, title, f"还有 {len(cases) - limit} 个"))
    publish(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
