# Information and Navigation Roadmap

**Owner:** Genuinely unfinished work after the unified information/navigation
cutover.

The current implementation already provides coherent information documents,
derived retrieval, known-topology projection, recursive route composition,
`navigate_to`/`NAVIGATE`, and direct-experience route learning. See
[Architecture](../ARCHITECTURE.md) and
[Runtime semantics](../RUNTIME.md) for settled behavior. The completed proposal
and migration phases are archived as
[Information and Navigation Plan](../legacy/plans/INFORMATION_AND_NAVIGATION_PLAN.md).

## Remaining work

### Information retrieval

- Expand retrieved anchors into richer coherent document neighborhoods instead
  of relying primarily on current passage projections.
- Add explicit contradiction presentation so authoritative dossier facts,
  observations, communicated claims, memories, and summaries remain visibly
  distinct.
- Evaluate when bounded retrieval can replace full-dossier controller context
  without losing necessary identity information.
- Extend transactional projection rebuild to all normalized information and
  memory lifecycle tables.

### Character-known topology

- Learn place and route knowledge from communication and richer perception, not
  only scenario initialization and direct navigation experience.
- Represent stale, incomplete, or incorrect route claims explicitly and test
  their interaction with authoritative execution.
- Improve destination discovery when a character knows a place concept but not
  a complete locator.

### Topology and execution

- Add execution adapters for general registered transitions beyond current
  room grids, portals, building entrances, and sparse city travel.
- Complete metro headway, line, transfer, and service-status semantics.
- Add exterior perceptible facts and spatial indexes so city-scale sensing
  scales with nearby entities rather than total scenario size.

### Validation and measurement

- Add focused tests for conflicting claims, communicated route learning,
  incomplete/incorrect known routes, and retrieval coherence.
- Measure retrieval quality, prompt size, route-planning cost, and dataset
  growth on representative large scenarios.

## Not current roadmap items

Online/digital-space execution, unrestricted network tools, approximate
background populations, and live checkpoint/resume remain out of Stage 0
scope. They require separate proposals rather than extension of this roadmap.
