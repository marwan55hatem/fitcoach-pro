"""Print a pass/fail report of the coach evals (offline: no real AI is called).

cd backend
.\\.venv\\Scripts\\python.exe -m tests.evals.run
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).parent


class _Collector:
    def __init__(self) -> None:
        self.results: dict[str, str] = {}

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "call" or (report.when == "setup" and report.failed):
            name = (
                report.nodeid.split("[", 1)[-1].rstrip("]")
                if "[" in report.nodeid
                else ""
            )
            if name:
                self.results[name] = report.outcome


def main() -> int:
    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    collector = _Collector()
    code = pytest.main(
        [
            str(HERE / "test_evals.py"),
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
            "-rN",
        ],
        plugins=[collector],
    )
    passed = sum(1 for r in collector.results.values() if r == "passed")
    print("\nCoach evals (offline)\n")
    for s in scenarios:
        outcome = collector.results.get(s["id"], "not run")
        mark = "PASS" if outcome == "passed" else "FAIL"
        print(f"  {mark}  {s['id']:<32} {s['about']}")
    print(f"\n{passed}/{len(scenarios)} passed")
    return 0 if code == 0 and passed == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(main())
