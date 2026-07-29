"""Source role classification and evidence preference (§5.3 step 6).

Not all code is equally authoritative about *how a system works*. A conftest
fixture and a demo script are real code, and they are often the strongest lexical
and semantic match for "how does X work" — they name every component and wire
them together in one readable place. But they describe the **test rig**, not the
system: they deliberately substitute fakes for the real thing.

That is not hypothetical. Asked "give the data flow for LLM usage in vectural",
retrieval returned `tests/conftest.py` and `demo.py` as its top evidence, and the
answer faithfully reported that LLM calls go through a `FakeGatewayClient` — true
of the demo, false of production, and impossible for the reader to tell apart.

So production code is *preferred*, not exclusive: tests still appear when they
are the best (or only) evidence, they just stop crowding out the real
implementation. Two escape hatches keep that honest — a question that is *about*
tests disables the preference entirely, and the fill-up rule below means thin
production evidence is topped up rather than left short.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath

from backend.retrieval.base import SearchHit


class SourceRole(StrEnum):
    PRODUCTION = "production"
    TEST = "test"
    DEMO = "demo"


_TEST_DIRS = {"tests", "test", "__tests__", "spec", "e2e", "testdata", "fixtures"}
_TEST_NAMES = re.compile(r"^(conftest\.py|.*_test\.[a-z]+|test_.*\..+|.*\.(test|spec)\.[jt]sx?)$")
_DEMO_DIRS = {"demo", "demos", "example", "examples", "sample", "samples", "sample-estate"}
_DEMO_NAMES = {"demo.py", "example.py", "sample.py"}

# Words that mean the asker actually wants the test rig. Then the preference is
# not just unhelpful, it is wrong.
# Stems are deliberately uneven. `test\w*` is safe (test/tested/testing/testable),
# but `spec\w*` would swallow "specific"/"specification" and silently disable the
# preference on ordinary questions, so spec stays exact.
_ABOUT_TESTS = re.compile(
    r"\b(test\w*|conftest|fixtures?|mock\w*|stub\w*|fake\w*|specs?|demos?|"
    r"examples?|samples?)\b",
    re.IGNORECASE,
)


def classify(path: str) -> SourceRole:
    """The role of a file, from its path alone — no index change required."""
    parts = PurePosixPath(path).parts
    name = parts[-1].lower() if parts else ""
    lowered = {p.lower() for p in parts[:-1]}

    if lowered & _TEST_DIRS or _TEST_NAMES.match(name):
        return SourceRole.TEST
    if lowered & _DEMO_DIRS or name in _DEMO_NAMES:
        return SourceRole.DEMO
    return SourceRole.PRODUCTION


def query_wants_tests(query: str) -> bool:
    """Whether the question is itself about tests/demos."""
    return bool(_ABOUT_TESTS.search(query))


def prefer_production(hits: list[SearchHit], *, top_n: int, query: str) -> list[SearchHit]:
    """Reorder so production code fills the evidence budget first.

    A stable partition, not a filter: relevance order is preserved *within* each
    group, and test/demo hits immediately top up whatever production evidence
    does not fill. So a question with little production evidence still gets a
    full answer, and a question about tests is left untouched.
    """
    if top_n <= 0 or query_wants_tests(query):
        return hits[:top_n]

    production = [h for h in hits if classify(h.path) is SourceRole.PRODUCTION]
    supporting = [h for h in hits if classify(h.path) is not SourceRole.PRODUCTION]
    return (production + supporting)[:top_n]
