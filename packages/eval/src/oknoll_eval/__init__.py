"""OpenKnoll PD-vs-RAG descriptive benchmark harness.

Frozen questions + pre-written gold evidence → `run_benchmark` over both
retrieval conditions → a descriptive comparison table. Deliberately not a
controlled study: no blinding, no statistics, no superiority claims; trace and
metric formats are retained so a future controlled evaluation can reuse them.
"""

from oknoll_eval.benchmark import (
    QUESTION_CLASSES,
    Benchmark,
    BenchmarkError,
    Question,
    load_benchmark,
)
from oknoll_eval.report import render_report
from oknoll_eval.runner import CONDITIONS, run_benchmark

__version__ = "0.3.3"

__all__ = [
    "CONDITIONS",
    "QUESTION_CLASSES",
    "Benchmark",
    "BenchmarkError",
    "Question",
    "__version__",
    "load_benchmark",
    "render_report",
    "run_benchmark",
]
