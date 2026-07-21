"""Code-aware tokenisation (implementation-plan §4.3).

Mirrors the OpenSearch ``code_analyzer`` (``word_delimiter_graph`` with
``catenate_words``, ``split_on_case_change``, ``split_on_numerics``, plus
``preserve_original`` and ``lowercase``) closely enough that the offline
in-memory retrieval backend ranks the same way the real cluster would: a search
for "refund reversal" matches an identifier ``processRefundReversal``.

Keeping one tokeniser here means the analyzer's *intent* is expressed once and
reused, rather than reimplemented differently in the index config and the
offline scorer.
"""

from __future__ import annotations

import re

# Runs of letters, digits, or a single sub-token boundary. We split further below.
_WORD = re.compile(r"[A-Za-z0-9]+")
# camelCase / PascalCase boundary, and letter<->digit boundaries.
_CAMEL = re.compile(
    r".+?(?:(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])|$)"
)


def code_tokens(text: str) -> list[str]:
    """Tokenise code/text into lowercased search terms.

    For each maximal alphanumeric run we emit the whole run (``preserve_original``
    + ``catenate_words``) plus its camel/numeric sub-tokens, all lowercased and
    de-duplicated while preserving first-seen order.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _emit(token: str) -> None:
        low = token.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(low)

    for run in _WORD.findall(text):
        _emit(run)
        parts = [m.group(0) for m in _CAMEL.finditer(run)]
        if len(parts) > 1:
            for part in parts:
                _emit(part)
    return out
