# SYNAPSE — operator decisions, recorded as made

## 2026-08-13 — the placement percentile and the boundary walk (operator)

**Placement suggestions use the 70th percentile** of observed working-link
distances (constant: PLACEMENT_PERCENTILE = 70). Provenance: operator,
2026-08-13, choosing between the existing display halo's 90th (optimistic)
and the handover draft's 25th (conservative). The SCAN display halo keeps
its 90th; the two answer different questions and are labelled.

**The on-site test mode** (specification, operator's words paraphrased):
- When the operator arrives at a suggested location carrying the new node,
  the medic offers a TEST: is this node reaching the existing mesh?
- NOT reaching -> the medic says WHICH DIRECTION to go: back toward the
  existing mesh (bearing to the nearest heard node).
- Reaching -> offer EXPLORE BOUNDARY mode: the operator walks AWAY from the
  mesh while the medic pings every 20 seconds. The moment the carried node
  loses the mesh, the medic screen FLASHES yellow/black with
  "Mesh Connection Lost".
- Every boundary loss is RECORDED: a measured link failure at a known
  distance — the negative evidence the range model cannot get any other
  way (survivorship-bias fix). Boundary walks feed the learned model
  alongside first_link's walk protocol, which this extends.

Open (not yet decided): the ranked telemetry collection additions; the
three-defaults spine; antenna/height question at birth.
