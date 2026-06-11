"""
Map metrics to the inputs that feed them, classify both metrics and inputs by
warehouse process, and flag formula tokens that have no matching input on the
`Inputs` sheet.

Matching strategy
-----------------
Formulas are written in informal English (``singles s/m volume``, ``HOV rate``,
``SLAP L``).  We tokenise the formula text and the canonical input names,
expand a small synonym dictionary, and look for substring / token matches.

The result for each metric is:

* ``inputs``           - list of canonical inputs found in the formula
* ``missing_tokens``   - phrases that look like inputs but are absent from
                         the `Inputs` sheet (these are surfaced to the user)
* ``main_process``     - one of Inbound / Outbound / C-Return / TSI / TSO /
                         V-Return / IXD - Cross Transshipment Dock
* ``core_process``     - Receive / Stow / Pick / Sort / Pack / Receive Dock /
                         Transfer In Stow / C-Return Stow / V-Return Pick /
                         Transfer Out Pick / Other
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple

from parser import InputItem, Metric


# ---------------------------------------------------------------------------
# Synonyms
# ---------------------------------------------------------------------------
#
# Maps an alias (lower case, possibly with spaces) to the canonical input name
# that lives on the `Inputs` sheet.  Aliases are searched for as whole word /
# phrase substrings inside formulas.  Order matters - longer aliases are tried
# first so that ``slap large`` wins over ``slap``.

ALIAS_TO_CANONICAL: List[Tuple[str, str]] = [
    # --- size / pack-path inputs --------------------------------------------
    ("singles s/m", "Single M/S Sortable %"),
    ("singles m/s", "Single M/S Sortable %"),
    ("single m/s", "Single M/S Sortable %"),
    ("single s/m", "Single M/S Sortable %"),
    ("single large", "Single Large %"),
    ("singles large", "Single Large %"),
    ("hov large", "HOV Large%"),
    ("hov", "HOV Large%"),
    ("slap large", "SLAP Large %"),
    ("slap l", "SLAP Large %"),
    ("slap medium", "SLAP Medium %"),
    ("slap m", "SLAP Medium %"),
    ("slap", "SLAP Medium %"),
    ("multi large", "Multi Large %"),
    ("ml ", "Multi Large %"),
    ("multi sortable", "Multi Sortable %"),
    ("multi medium", "Multi Sortable %"),
    ("mm/s", "Multi Sortable %"),
    ("mm ", "Multi Sortable %"),
    ("multi tamper proof", "Multi Tamper Proof %"),
    ("mtp", "Multi Tamper Proof %"),
    ("tp ", "Multi Tamper Proof %"),
    ("ns within ml", "NS within ML %"),
    ("singles%", "Singles%"),
    ("multis%", "Multis%"),
    # --- pick / pack rates --------------------------------------------------
    ("single m/s sortable pick", "Single M/S Sortable Pick rate"),
    ("singles s/m rate", "Single M/S Sortable Pick rate"),
    ("hov rate", "HOV Large Actual pick rate"),
    ("slap rate", "SLAP Medium Actual pick rate"),
    ("tp rate", "Multi Tamper Proof Actual pick rate"),
    ("ml sort batch rate", "Multi Large Actual rebin rate"),
    ("ml1ppb rate", "ML1PPB rate"),  # MISSING - flagged
    ("mm/s sort batch rate", "Multi Sortable Actual rebin rate"),
    ("mtp sort rate", "Multi Tamper Proof Actual rebin rate"),
    ("multi m pack rate", "Multi Sortable Actual Pack rate"),
    ("ml pack rate", "Multi Large Actual Pack rate"),
    ("mtp pack rate", "Multi Tamper Proof Actual Pack rate"),
    ("each receive rate", "Each recieve rate"),
    ("non sort each receive rate", "Non-sort Each receive rate"),
    ("case receive rate", "Case receive rate"),
    ("prep rate ns", "Prep Rate NS"),
    ("prep rate", "Prep Rate"),
    ("blended stow rate", "Blended stow rate"),
    ("case stow rate", "Case stow rate "),
    ("stow rate non-sort", "Stow rate non-sort"),
    # --- volume / mix inputs ------------------------------------------------
    ("singles volume", "Singles%"),
    ("multis volume", "Multis%"),
    ("hov volume", "HOV Large%"),
    ("slap volume", "SLAP Medium %"),
    ("tp volume", "Multi Tamper Proof %"),
    ("multi m volume", "Multi Sortable %"),
    ("ml volume", "Multi Large %"),
    ("mtp volume", "Multi Tamper Proof %"),
    ("each receive volume", "Each receive%"),
    ("case receive volume", "Case receive %"),
    ("pallet receive volume", "Pallet receive %"),
    ("lp receive volume", "LP Receive%"),
    ("non sort each receive%", "% Non sort Each receive"),
    ("non sort prep", "% of Non sort Prep "),
    # --- batches / cycle / containers --------------------------------------
    ("ml unit per batch", "ML Unit Per Batch (UPB)"),
    ("mm upb", "MM UPB"),
    ("mtp upb", "MTP UPB"),
    ("batch cycle time ml", "Batch Cycle Time ML (hrs)"),
    ("batch cycle time mm", "Batch Cycle Time MM (hrs)"),
    ("batch cycle time mtp", "Batch Cycle Time MTP (hrs)"),
    ("ml rebin", "ML Rebin"),
    ("mm rebin", "MM Rebin"),
    ("mtp rebin", "MTP Rebin"),
    # --- containers and unit conversions ------------------------------------
    ("unit per case", "Unit per case"),
    ("units per case", "Unit per case"),
    ("unit per pallet", "Unit per pallet (Pallet receive)"),
    ("units per pallet", "Unit per pallet (Pallet receive)"),
    ("units per tote", "Units per tote"),
    ("special receive cart", "Units per Special receive cart"),
    ("upt tsi", "Unit per pallet TSI"),
    ("unit per tsi pallet", "Unit per pallet TSI"),
    # --- VRC / floor --------------------------------------------------------
    ("volume picked from each floor", "Volume picked from each floor(1,2,3,4,5,6)"),
    ("volume stowed in each floor", "Volume stowed in each floor (1,2,3,4,5,6)"),
    ("stacking filter", "Stacking filter based volume percentage"),
    ("shipments per pallet", "Shipments per pallet"),
    ("spp", "Shipments per pallet"),
    # --- VRETS --------------------------------------------------------------
    ("vrets singles", "VRETS Singles %"),
    ("vrets multis", "VRETS Multis %"),
    ("vrets ns", "% of NS VRETS"),
    ("vrets pick rate", "VRETs pick rate by process path"),
    ("vrets pack rate", "VRETS pack rate by process path"),
    ("vrets rebin rate", "VRETS Rebin rate by each process path"),
    ("upb vrets", "UPB VRETS by each process path"),
    ("vrets cycle time", "Cycle time VRET by each process path"),
    ("vrets upt", "VRETS UPT by each process path"),
    ("vrets ups", "VRETS UPT by each process path"),
    ("vrets volume", "VRETS volume by each process path"),
    # --- TSO ----------------------------------------------------------------
    ("tso ns%", "TSO NS%"),
    ("tso volume", "TSO volume by each process path"),
    ("upt tso", "UPT TSO  by each process path"),
    ("tso pick rate", "TSO Pick rate"),
    ("tso arc", "TSO Arc based volume percentage"),
    ("units per pallet tso", "Units per pallet"),
    # --- shipment-level inputs ---------------------------------------------
    ("blended ups", "Blended UPS (Units Per Shipment)"),
    ("multis ob cs", "Multis OB CS (Units per shipment)"),
    ("multis ups", "Multis OB CS (Units per shipment)"),
    # --- container size ----------------------------------------------------
    ("small stow units", "Small Stow dashboard units per container"),
    ("medium stow units", "Medium Stow dashboard units per container"),
    ("large stow units", "Large Stow dashboard units per container"),
    ("xl stow units", "XL Stow dashboard units per container"),
]


# Aliases that look like real inputs in the formulas but have NO matching row
# on the `Inputs` sheet.  We surface these to the user as "missing input".
KNOWN_MISSING: Dict[str, str] = {
    "ml1ppb": "ML1PPB volume / rate (mentioned in 'Rebin Stations (NS/MP1PPB)')",
    "mppb": "MPPB process path (UPB, UPT, batch cycle time)",
    "sppb": "SPPB process path (UPB, UPT)",
    "rds": "Removal Damage Singles (large/medium/small) units per pallet",
    "destroy large": "Destroy large units per pallet (RDS staging)",
    "destroy medium": "Destroy medium units per pallet",
    "destroy small": "Destroy small units per pallet",
    "ags volume": "AGS hourly volume",
    "absheer volume": "Absheer hourly volume",
    "absheer hourly": "Absheer hourly volume",
    "ags hourly": "AGS hourly volume",
    "upp": "Units per pallet (UPP) for iXD / CRETs / Absheer / AGS",
    "upp(case)": "Units per pallet (case) at iXD",
    "upp(pallet)": "Units per pallet (pallet) at iXD",
    "upp crets": "Units per pallet for CRETs",
    "fclm": "Unit per pallet from FCLM",
    "dockmaster": "Unit per pallet from Dockmaster",
    "cs pick tower": "CS pick tower percentage",
    "vrets pick tower": "VRETs pick tower percentage",
    "transshipment pick tower": "Transshipment pick tower percentage",
    "cs pallet tower": "CS pallet tower percentage",
    "vrets pallet tower": "VRETs pallet tower percentage",
    "transshipment pallet tower": "Transshipment pallet tower percentage",
    "totes generated": "Totes generated per process path",
    "wrangle carts per batch": "Wrangle carts per batch",
    "ns carts per batch": "Non-sort carts per batch",
    "batches per hour": "Batches per hour",
    "batches per cycle": "Batches per cycle",
    "loading time": "VRC loading time (seconds)",
    "travelling time": "VRC travelling time per floor",
    "container per move": "Containers per move",
    "tso reactive %": "TSO Reactive % (and UPT)",
    "tso proactive %": "TSO Proactive % (and UPT)",
    "site stacking rate": "Site stacking rate",
    "as staffed": "Aas staffed (sorter)",
    "aas staffed": "Aas staffed (sorter)",
    "truck pallet count": "Truck pallet count capacity (4t/10t/24t)",
    "staging time requirement": "iXD SC staging time requirement",
}


# ---------------------------------------------------------------------------
# Main process / Core process classification
# ---------------------------------------------------------------------------

MAIN_PROCESS_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("Outbound", [
        "pack station", "rebin", "tote sorter", "singles line", "shipping sorter",
        "pick conveyor", "pallet tower", "merge line", "fm injection", "sc staging",
        "sc stacking", "buffer spots (ce)", "g pick", "g+1", "g+2", "g+3", "g+4",
    ]),
    ("V-Return", ["vrets", "vret"]),
    ("C-Return", ["crets", "cret"]),
    ("TSO", ["tso"]),
    ("TSI", ["tsi"]),
    ("Inbound", [
        "receive stations", "nyr", "ixd", "absheer", "ags", "receive dock",
        "case receive", "each receive", "pallet receive", "prep",
    ]),
]

CORE_PROCESS_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("Pack", ["pack station", "pack rate"]),
    ("Sort", ["tote sorter", "shipping sorter", "rebin", "stacking", "sort pallet", "merge line", "fm injection"]),
    ("Pick", ["pick conveyor", "pick tower", "pick rate", "pallet tower"]),
    ("Stow", ["stow"]),
    ("Receive Dock", ["nyr", "tsi spots", "ixd", "absheer", "ags", "dock"]),
    ("Receive", ["receive stations", "case receive", "each receive", "pallet receive", "prep"]),
    ("V-Return Pick", ["vret"]),
    ("Transfer Out Pick", ["tso"]),
    ("C-Return Stow", ["cret"]),
    ("Transfer In Stow", ["tsi"]),
]


def classify_main_process(metric_name: str) -> str:
    text = metric_name.lower()
    for label, kws in MAIN_PROCESS_KEYWORDS:
        if any(kw in text for kw in kws):
            return label
    return "IXD - Cross Transshipment Dock"


def classify_core_process(metric_name: str, formula: str) -> str:
    haystack = (metric_name + " " + formula).lower()
    for label, kws in CORE_PROCESS_KEYWORDS:
        if any(kw in haystack for kw in kws):
            return label
    return "Other"


# ---------------------------------------------------------------------------
# Dependency extraction
# ---------------------------------------------------------------------------

@dataclass
class MetricDeps:
    metric: Metric
    inputs: List[str] = field(default_factory=list)         # canonical input names
    missing_tokens: List[str] = field(default_factory=list) # human description
    main_process: str = "IXD - Cross Transshipment Dock"
    core_process: str = "Other"


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text


def find_inputs_in_formula(
    formula: str, inputs: List[InputItem]
) -> Tuple[List[str], List[str]]:
    """Return (matched canonical input names, missing-token descriptions)."""

    if not formula:
        return [], []

    text = _normalize(formula)
    matched: List[str] = []
    seen: Set[str] = set()

    # 1) Alias-based matches.  Sort longest-first so "slap large" is tried
    #    before "slap".
    for alias, canonical in sorted(ALIAS_TO_CANONICAL, key=lambda kv: -len(kv[0])):
        if alias in text and canonical not in seen:
            matched.append(canonical)
            seen.add(canonical)

    # 2) Direct substring match against canonical input names (case-insensitive).
    for inp in inputs:
        nm = _normalize(inp.name)
        # require the input name to be at least 4 chars to avoid noise
        if len(nm) >= 4 and nm in text and inp.name not in seen:
            matched.append(inp.name)
            seen.add(inp.name)

    # 3) Detect "missing" tokens that look like inputs but aren't present.
    missing: List[str] = []
    for token, description in KNOWN_MISSING.items():
        if token in text:
            # Only flag as missing if no canonical alias already covered it.
            if not any(token in _normalize(c) for c in matched):
                missing.append(description)

    # Filter matched to only those that are actually in the inputs sheet so
    # the Sankey diagram can resolve them; aliases that point at non-existent
    # inputs become missing tokens instead.
    canonical_names = {inp.name for inp in inputs}
    real_matched: List[str] = []
    for name in matched:
        if name in canonical_names:
            real_matched.append(name)
        else:
            missing.append(f"{name} (referenced in formula, not on Inputs sheet)")

    # Deduplicate while preserving order
    seen_m = set()
    unique_missing = []
    for m in missing:
        if m not in seen_m:
            unique_missing.append(m)
            seen_m.add(m)

    return real_matched, unique_missing


def build_dependencies(
    metrics: Iterable[Metric], inputs: List[InputItem]
) -> List[MetricDeps]:
    out: List[MetricDeps] = []
    for m in metrics:
        formula_text = m.full_text
        matched, missing = find_inputs_in_formula(formula_text, inputs)
        out.append(
            MetricDeps(
                metric=m,
                inputs=matched,
                missing_tokens=missing,
                main_process=classify_main_process(m.name),
                core_process=classify_core_process(m.name, formula_text),
            )
        )
    return out


if __name__ == "__main__":
    from parser import parse_workbook
    from pathlib import Path

    book = parse_workbook(Path(
        r"C:\Users\amdtarek\Documents\MCP 2.0\APC dashboard Formulas Rev NC.xlsx"
    ))
    deps = build_dependencies(book.metrics_by_warehouse["RUH8"], book.inputs)
    print(f"Total metrics: {len(deps)}")
    for d in deps[:8]:
        print(f"\n* {d.metric.name}  [{d.main_process} / {d.core_process}]")
        print(f"  formula : {d.metric.primary_formula}")
        print(f"  inputs  : {d.inputs}")
        print(f"  missing : {d.missing_tokens}")

    # Quick stats
    by_main: Dict[str, int] = {}
    by_core: Dict[str, int] = {}
    n_missing = 0
    for d in deps:
        by_main[d.main_process] = by_main.get(d.main_process, 0) + 1
        by_core[d.core_process] = by_core.get(d.core_process, 0) + 1
        if d.missing_tokens:
            n_missing += 1
    print("\nMain process counts:", by_main)
    print("Core process counts:", by_core)
    print(f"Metrics with missing inputs: {n_missing}/{len(deps)}")
