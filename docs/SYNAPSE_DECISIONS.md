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

## 2026-08-13 — the range-probe companion, and honest map edges (operator)

**A dedicated range-finding node joins the medic kit**: like Jonesy —
known hardware, always with the medic — but DETACHABLE, carried out for the
boundary walk / on-site test mode. Candidate board: the Seeed XIAO ESP32S3 +
Wio-SX1262 (verified flash-ready 2026-08-13, model 0xDD covers 915.125).
A known-hardware probe makes every walk sample calibrated data. (ttt is off
power for now; the probe replaces ad-hoc test nodes.)

**SCAN edges are typed by transport, and LoRa is the standard view.**
The current screen drew Wi-Fi RSSI as edge strength — assumption dressed as
data, now to be fixed. Rules: LoRa edges are the default view, always on,
no off switch. Wi-Fi, Bluetooth and Internet connections are toggleable
overlays (slot switches), OFF-able because Wi-Fi range means "same
building" and internet reach negates the need for infill nodes — they are
not placement signal. Non-LoRa links draw in a different colour per
transport; an edge's strength is only ever drawn from a measurement OF THAT
transport, or by existence alone when there is none.
