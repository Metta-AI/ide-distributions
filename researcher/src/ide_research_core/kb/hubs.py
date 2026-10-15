"""Hub health flags and the maintenance cards they produce (System Design §3.5).

``hub_flags`` is pure. Codes (``Finding.entity`` is the hub id; for ``HUB_OVERLAP`` it is the
two hub ids, sorted and comma-joined):

- ``HUB_TOO_LARGE``: the hub file is over ``size_limit_tokens`` (``len(text) // 4``) -- split it.
- ``HUB_DRIFT``: the claims the hub cites that exist form at least two weakly connected
  components of at least three claims each (claims linked by claim-to-claim citations) -- the
  hub covers more than one subject, split it.
- ``HUB_OVERLAP``: two hubs' cited-claim sets have Jaccard similarity of at least 0.6 -- merge them.

``request_hub_maintenance`` writes one P2 maintenance card request per finding. The request's
``why`` carries a marker ``[hub:<code>:<entity>]``; a finding whose marker is already in
``cards/requests`` gets no second request.
"""

from itertools import combinations

from ide_research_core.kb.index import estimate_tokens
from ide_research_core.kb.lint import Finding, cited_claims, load_records
from ide_research_core.kb.requests import bracketed, has_marker, iter_request_specs
from ide_research_core.paths import Layout
from ide_research_core.queue.card import CardSpec
from ide_research_core.queue.requests import write_request
from ide_research_core.records.registry import path_for

HUB_TOO_LARGE = "HUB_TOO_LARGE"
HUB_DRIFT = "HUB_DRIFT"
HUB_OVERLAP = "HUB_OVERLAP"

MIN_COMPONENT_CLAIMS = 3
OVERLAP_THRESHOLD = 0.6
_ACTION = {
    HUB_TOO_LARGE: "Split",
    HUB_DRIFT: "Split",
    HUB_OVERLAP: "Merge",
}


def hub_flags(layout: Layout, size_limit_tokens: int = 2000) -> list[Finding]:
    hubs = load_records(layout, "hub")
    claims = {claim.id: claim for claim in load_records(layout, "claim")}
    cited = {hub.id: cited_claims(hub) & claims.keys() for hub in hubs}
    claim_links = {
        claim_id: cited_claims(claim) & claims.keys() for claim_id, claim in claims.items()
    }
    findings = []
    for hub in sorted(hubs, key=lambda h: h.id):
        tokens = estimate_tokens(path_for(layout, hub.id).read_text(encoding="utf-8"))
        if tokens > size_limit_tokens:
            findings.append(
                Finding(HUB_TOO_LARGE, hub.id, f"about {tokens} tokens, over {size_limit_tokens}")
            )
        sizes = _component_sizes(cited[hub.id], claim_links)
        if sum(size >= MIN_COMPONENT_CLAIMS for size in sizes) >= 2:
            findings.append(
                Finding(HUB_DRIFT, hub.id, f"cited claims form separate groups of sizes {sizes}")
            )
    for first, second in combinations(sorted(hubs, key=lambda h: h.id), 2):
        union = cited[first.id] | cited[second.id]
        if not union:
            continue
        overlap = len(cited[first.id] & cited[second.id]) / len(union)
        if overlap >= OVERLAP_THRESHOLD:
            findings.append(
                Finding(
                    HUB_OVERLAP, f"{first.id},{second.id}", f"cited-claim overlap {overlap:.2f}"
                )
            )
    return sorted(findings)


def _component_sizes(nodes: set[str], links: dict[str, set[str]]) -> list[int]:
    """Sizes (descending) of the weakly connected components of ``nodes``, joined by ``links``
    in either direction and only through nodes in the set."""
    neighbours: dict[str, set[str]] = {node: set() for node in nodes}
    for node in nodes:
        for other in links.get(node, set()) & nodes:
            neighbours[node].add(other)
            neighbours[other].add(node)
    seen: set[str] = set()
    sizes = []
    for start in sorted(nodes):
        if start in seen:
            continue
        stack, size = [start], 0
        seen.add(start)
        while stack:
            node = stack.pop()
            size += 1
            for other in neighbours[node] - seen:
                seen.add(other)
                stack.append(other)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def _marker(finding: Finding) -> str:
    return f"hub:{finding.code}:{finding.entity}"


def request_hub_maintenance(layout: Layout, findings: list[Finding]) -> list[str]:
    """Write a P2 maintenance request per hub finding not yet requested; return the Q ids."""
    request_ids = []
    for finding in findings:
        marker = _marker(finding)
        if any(has_marker(spec, marker) for spec in iter_request_specs(layout)):
            continue
        hub_ids = finding.entity.split(",")
        spec = CardSpec(
            request_id="",  # write_request assigns the id
            title=f"{_ACTION[finding.code]} hub {' and '.join(hub_ids)}",
            mode="maintenance",
            why=f"{finding.code}: {finding.message} {bracketed(marker)}",
            refs=[f"ar://hub/{hub_id}" for hub_id in hub_ids],
            created_by="kb-hub-check",
            priority="P2",
        )
        request_ids.append(write_request(layout, "create", spec=spec))
    return request_ids
