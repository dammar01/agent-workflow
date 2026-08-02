"""Read-only index over `graphify-out/graph.json`.

Deliberately parses graph.json directly rather than shelling out to `graphify query`
or running its MCP server, avoiding an extra process and a PATH dependency.

Edge confidence is honored: graphify tags every edge EXTRACTED (read from source),
INFERRED, or AMBIGUOUS. Treating an inferred edge as strong as an extracted one is
how a shortlist starts pointing at files that were never really related.
"""
import json
import os
import re
from pathlib import Path

GRAPH_DIRNAME = "graphify-out"
GRAPH_FILENAME = "graph.json"
MAX_LEADS = 12
MAX_COMMUNITIES = 4

# Skipped when dating the sources: vendored trees can hold thousands of .py files that
# say nothing about whether THIS project's graph is out of date.
_SKIP_DIRS = {
    "__pycache__", ".git", GRAPH_DIRNAME, "storage", ".workflow",
    "node_modules", "venv", ".venv", "site-packages", "dist", "build", ".tox",
}

# How much an edge counts toward a node's weight, by graphify's own confidence tag.
_CONFIDENCE_WEIGHT = {
    "extracted": 1.0,
    "inferred": 0.5,
    "ambiguous": 0.25,
}
_DEFAULT_CONFIDENCE_WEIGHT = 0.5

_WORD = re.compile(r"[A-Za-z0-9]+")
# job_manager / JobManager both have to yield {job, manager}, otherwise a hint like
# "job manager reaper" scores zero against the very file it describes.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "what",
    "which", "yang", "untuk", "dari", "dengan", "pada", "atau", "adalah", "juga",
    "bagaimana", "kenapa", "apakah", "agar", "serta", "dalam", "akan", "bisa",
    "code", "file", "files", "kode", "project", "codebase",
}


def graph_path(project_root) -> Path:
    return Path(project_root) / GRAPH_DIRNAME / GRAPH_FILENAME


def load_graph(project_root) -> dict | None:
    """Parsed graph.json, or None when absent/unreadable. Never raises."""
    path = graph_path(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# (project_root, graph_mtime) -> verdict. The check walks every .py in the repo, and
# it runs on every delegated call; without this the same full-tree scan is repeated for
# a graph that has not moved. Keyed on the graph's mtime so a regenerated graph is
# always re-evaluated. Process-local and tiny — a worker handles one call and exits.
_STALE_CACHE: dict[tuple[str, float], bool | None] = {}

# ...which is exactly why the in-process cache never helped: every delegated call is a
# FRESH worker process, so the memo was always empty and the full-tree walk ran every
# single time. The verdict is a pure function of the graph mtime, so it survives on disk.
_STALE_CACHE_FILE = "graph-stale.json"


def _stale_cache_path(project_root) -> Path:
    return Path(project_root) / ".workflow" / _STALE_CACHE_FILE


def _read_stale_cache(project_root, graph_mtime: float) -> bool | None:
    """Cached verdict for this exact graph mtime, or None to recompute. Never raises."""
    try:
        data = json.loads(
            _stale_cache_path(project_root).read_text(encoding="utf-8")
        )
        if float(data.get("graph_mtime", -1)) == graph_mtime:
            verdict = data.get("verdict")
            return verdict if isinstance(verdict, bool) else None
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _write_stale_cache(project_root, graph_mtime: float, verdict: bool) -> None:
    """Best-effort: a cache that cannot be written must not fail the call."""
    try:
        path = _stale_cache_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"graph_mtime": graph_mtime, "verdict": verdict}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass


def is_stale(project_root) -> bool | None:
    """True when the graph is older than the newest tracked source file.

    None when it cannot be determined. A stale graph is still useful as leads, but
    the caller must be able to say so rather than presenting it as current.
    """
    path = graph_path(project_root)
    if not path.exists():
        return None
    try:
        graph_mtime = path.stat().st_mtime
    except OSError:
        return None

    cache_key = (str(project_root), graph_mtime)
    if cache_key in _STALE_CACHE:
        return _STALE_CACHE[cache_key]
    cached = _read_stale_cache(project_root, graph_mtime)
    if cached is not None:
        _STALE_CACHE[cache_key] = cached
        return cached

    newest = 0.0
    root = Path(project_root)
    try:
        # rglob itself can raise (permission denied on a subtree) — this runs on every
        # delegated call, so it must degrade to "unknown" rather than break the call.
        for candidate in root.rglob("*.py"):
            if set(candidate.parts) & _SKIP_DIRS:
                continue
            try:
                newest = max(newest, candidate.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return None
    if not newest:
        return None
    verdict = newest > graph_mtime
    _STALE_CACHE[cache_key] = verdict
    _write_stale_cache(project_root, graph_mtime, verdict)
    return verdict


def _relative_source(source_file: str | None, project_root) -> str | None:
    """graph.json stores absolute OS-native paths; emit repo-relative POSIX ones so the
    shortlist means the same thing on another machine (and matches how file:line
    citations are written everywhere else)."""
    if not source_file:
        return None
    try:
        rel = Path(source_file).resolve().relative_to(Path(project_root).resolve())
        return rel.as_posix()
    except (ValueError, OSError):
        return Path(source_file).name or None


def _nodes(graph: dict) -> list[dict]:
    nodes = graph.get("nodes")
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _links(graph: dict) -> list[dict]:
    for key in ("links", "edges"):
        value = graph.get(key)
        if isinstance(value, list):
            return [e for e in value if isinstance(e, dict)]
    return []


def _edge_endpoints(edge: dict) -> tuple[str | None, str | None]:
    src = edge.get("source", edge.get("_src"))
    tgt = edge.get("target", edge.get("_tgt"))
    return (str(src) if src is not None else None, str(tgt) if tgt is not None else None)


def _edge_weight(edge: dict) -> float:
    tag = str(edge.get("confidence") or "").strip().lower()
    base = _CONFIDENCE_WEIGHT.get(tag, _DEFAULT_CONFIDENCE_WEIGHT)
    try:
        score = float(edge.get("confidence_score"))
    except (TypeError, ValueError):
        score = None
    return base * score if score is not None and 0 < score <= 1 else base


def _degrees(graph: dict) -> dict[str, float]:
    """Confidence-weighted degree per node id."""
    degrees: dict[str, float] = {}
    for edge in _links(graph):
        weight = _edge_weight(edge)
        for endpoint in _edge_endpoints(edge):
            if endpoint:
                degrees[endpoint] = degrees.get(endpoint, 0.0) + weight
    return degrees


def _keywords(text: str) -> set[str]:
    words: set[str] = set()
    for token in _WORD.findall(_CAMEL.sub(" ", text or "")):
        lowered = token.lower()
        if len(lowered) > 2 and lowered not in _STOPWORDS:
            words.add(lowered)
    return words


def entry_points(project_root, limit: int = MAX_LEADS) -> list[dict]:
    """Highest-connectivity nodes — the structural way into the codebase."""
    graph = load_graph(project_root)
    if not graph:
        return []
    degrees = _degrees(graph)
    ranked = sorted(
        _nodes(graph),
        key=lambda n: degrees.get(str(n.get("id")), 0.0),
        reverse=True,
    )
    return [_as_lead(n, project_root, degrees) for n in ranked[:limit]]


def files_in_community(project_root, community, graph: dict | None = None) -> list[str]:
    graph = graph if graph is not None else load_graph(project_root)
    if not graph:
        return []
    files: list[str] = []
    for node in _nodes(graph):
        if node.get("community") != community:
            continue
        rel = _relative_source(node.get("source_file"), project_root)
        if rel and rel not in files:
            files.append(rel)
    return files


def neighbors(project_root, node_id: str) -> list[str]:
    graph = load_graph(project_root)
    if not graph:
        return []
    found: list[str] = []
    for edge in _links(graph):
        src, tgt = _edge_endpoints(edge)
        other = tgt if src == str(node_id) else (src if tgt == str(node_id) else None)
        if other and other not in found:
            found.append(other)
    return found


def top_files(
    project_root, hint: str = "", limit: int = MAX_LEADS, graph: dict | None = None
) -> list[dict]:
    """Files ranked by keyword overlap with `hint`, then by weighted degree.

    Two ranking rules keep keyword relevance ahead of connectivity:

    1. Degree is normalised to the same 0-10 band as one keyword match, so
       connectivity remains a tiebreaker.
    2. Matches are aggregated ACROSS a file's nodes rather than taken from its single
       best one. graph.json carries no file contents; symbol labels (`JobManager`,
       `reap_stalled`) are its semantic signal.

    With no hint this degrades to pure connectivity ranking, which is still a far
    better starting point than an alphabetical directory listing.
    """
    graph = graph if graph is not None else load_graph(project_root)
    if not graph:
        return []
    degrees = _degrees(graph)
    wanted = _keywords(hint)
    max_degree = max(degrees.values(), default=0.0) or 1.0

    files: dict[str, dict] = {}
    for node in _nodes(graph):
        rel = _relative_source(node.get("source_file"), project_root)
        if not rel:
            continue
        label = str(node.get("label") or node.get("norm_label") or "")
        matched = (wanted & (_keywords(label) | _keywords(rel))) if wanted else set()
        degree = degrees.get(str(node.get("id")), 0.0)

        row = files.get(rel)
        if row is None:
            row = {
                "file": rel,
                "matched_terms": set(),
                "degree": 0.0,
                "community": node.get("community"),
                "label": label,
            }
            files[rel] = row
        row["matched_terms"] |= matched
        if degree > row["degree"]:
            # Keep the community and label of the file's most connected node: that is
            # the one whose cluster membership actually describes the file.
            row["degree"] = degree
            row["community"] = node.get("community")
            row["label"] = label

    ranked: list[dict] = []
    for row in files.values():
        overlap = len(row["matched_terms"])
        score = overlap * 10 + 10 * (row["degree"] / max_degree)
        ranked.append(
            {
                "file": row["file"],
                "score": round(score, 2),
                "matched": overlap,
                "matched_terms": sorted(row["matched_terms"])[:6],
                "community": row["community"],
                "label": row["label"],
            }
        )

    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:limit]


def _as_lead(node: dict, project_root, degrees: dict[str, float]) -> dict:
    return {
        "label": str(node.get("label") or node.get("norm_label") or node.get("id") or ""),
        "file": _relative_source(node.get("source_file"), project_root),
        "community": node.get("community"),
        "degree": round(degrees.get(str(node.get("id")), 0.0), 2),
    }


def leads(project_root, hint: str = "", limit: int = MAX_LEADS) -> dict | None:
    """Shortlist for one delegated call, or None when no graph exists.

    Shape: {'files': [...], 'communities': [...], 'stale': bool|None, 'node_count': int}
    """
    # Parse once and pass the graph to helpers used by this delegated call.
    graph = load_graph(project_root)
    if not graph:
        return None
    files = top_files(project_root, hint, limit, graph=graph)
    if not files:
        return None

    communities: list[dict] = []
    seen: set = set()
    for row in files:
        community = row.get("community")
        if community is None or community in seen:
            continue
        seen.add(community)
        members = files_in_community(project_root, community, graph=graph)
        if members:
            communities.append({"community": community, "files": members[:8]})
        if len(communities) >= MAX_COMMUNITIES:
            break

    return {
        "files": files,
        "communities": communities,
        "stale": is_stale(project_root),
        "node_count": len(_nodes(graph)),
        "edge_count": len(_links(graph)),
    }
