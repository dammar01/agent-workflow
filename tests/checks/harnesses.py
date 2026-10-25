"""Every research and staging harness imports against the pinned Exoma.

The harnesses reach into Exoma's internals (fact_store, metrics, transcript, osutil), not a
public API, so an Exoma refactor can break an experiment without any Exoma test failing.
Moving the pin in pyproject.toml runs this first: a harness that no longer imports fails
here, named, instead of halfway through a measurement.
"""

import importlib
import sys
from pathlib import Path

from tests.checks.support import assert_true

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESSES = (
    "tools.maintain.check_research",
    "tools.maintain.export_blind_sample",
    "tools.maintain.measure_graph_hook",
    "tools.maintain.measure_real_use",
    "tools.maintain.measure_task_outcomes",
    "tools.maintain.replay_facts",
    "tools.e2e.e2e_delegated",
)


def _test_harnesses_import_against_the_pin() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    broken = []
    for name in HARNESSES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — any import failure is the finding
            broken.append(f"{name}: {type(exc).__name__}: {exc}")
    assert_true(not broken, "harnesses that no longer import against the pinned Exoma: " + "; ".join(broken))

    from tools.maintain import measure_task_outcomes

    assert_true(
        measure_task_outcomes.INTENT_MAP.is_file(),
        f"the intent map the prompt classifier reads is not in the pinned bundle: {measure_task_outcomes.INTENT_MAP}",
    )
