"""Freshness / incremental reindex (implementation-plan §5.9, design-document §4.4).

A merge to main must be reflected in answers within the SLA, with no orphaned
data (R3). The pipeline, driven off a git diff:

- **added / modified** → re-parse, re-chunk, re-embed (skip unchanged content hashes)
- **deleted** → cascade: chunks from the index, nodes **and all referencing edges**
  from the graph
- **renamed** → delete + add, carrying the summary forward if the content hash matches

Cascade invalidation runs upward (file → module → service auto-regenerate), but
**flow narratives are only flagged ``needs_review``, never silently regenerated**
(§4.4, reusing the Phase 7 invalidation). Serving continues on the previous
version with a visible staleness indicator until the reindex completes.

Backstops: a nightly full-diff run for missed webhooks, and a periodic
reconciliation sweep that deletes index orphans no longer in the git tree.
"""

from backend.freshness.diff import ChangeStatus, FileChange, parse_name_status
from backend.freshness.reconcile import ReconcileReport, reconcile
from backend.freshness.reindex import Reindexer, ReindexResult, reindex
from backend.freshness.state import FreshnessState

__all__ = [
    "ChangeStatus",
    "FileChange",
    "FreshnessState",
    "ReconcileReport",
    "ReindexResult",
    "Reindexer",
    "parse_name_status",
    "reconcile",
    "reindex",
]
