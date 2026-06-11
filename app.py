"""
APC Formulas Dashboard - interactive Sankey of inputs into metrics.

Run with:
    streamlit run apc_dashboard/app.py

Layout:
    Sidebar  - Warehouse picker, Main Process / Core Process filters,
               metric drop-down, "show full hierarchy" toggle.
    Main     - Sankey diagram (Inputs -> Main Process -> Core Process ->
               Metric), formula breakdown, missing-input warnings.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Allow `streamlit run apc_dashboard/app.py` from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from parser import parse_workbook, Workbook, Metric, InputItem
from dependencies import (
    build_dependencies,
    MetricDeps,
    classify_main_process,
    classify_core_process,
)
from evaluator import extract_variables, evaluate


XLSX_DEFAULT = Path(__file__).resolve().parent.parent / "APC dashboard Formulas Rev NC.xlsx"

MAIN_PROCESS_ORDER = [
    "Inbound", "Outbound", "C-Return", "TSI", "TSO", "V-Return",
    "IXD - Cross Transshipment Dock",
]
CORE_PROCESS_ORDER = [
    "Receive", "Stow", "Pick", "Sort", "Pack", "Receive Dock",
    "Transfer In Stow", "C-Return Stow", "V-Return Pick",
    "Transfer Out Pick", "Other",
]

# Plotly's default qualitative palette, stretched
NODE_PALETTE = {
    "input": "#7FB3D5",        # muted blue
    "main":  "#F5B041",        # amber
    "core":  "#58D68D",        # green
    "metric":"#AF7AC5",        # purple
    "missing":"#E74C3C",       # red
}


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_workbook(path: str) -> Tuple[Workbook, Dict[str, List[MetricDeps]]]:
    book = parse_workbook(Path(path))
    deps_by_wh = {
        wh: build_dependencies(metrics, book.inputs)
        for wh, metrics in book.metrics_by_warehouse.items()
    }
    return book, deps_by_wh


# ---------------------------------------------------------------------------
# Sankey builder
# ---------------------------------------------------------------------------

def build_sankey(
    deps: List[MetricDeps],
    selected_metric: str | None,
    show_missing: bool,
) -> go.Figure:
    """Build a 2-layer Sankey: Input -> Metric.

    Missing inputs (referenced in formulas but not on the Inputs sheet) are
    drawn as red nodes when ``show_missing`` is True so the user can see
    which inputs are still required to capture the full dependency graph.
    """

    if selected_metric and selected_metric != "(All metrics)":
        deps = [d for d in deps if d.metric.name == selected_metric]

    inputs_layer: List[str] = []
    missing_layer: List[str] = []
    metric_layer: List[str] = []

    def add(layer: List[str], name: str) -> None:
        if name and name not in layer:
            layer.append(name)

    for d in deps:
        for inp in d.inputs:
            add(inputs_layer, inp)
        if show_missing:
            for m in d.missing_tokens:
                add(missing_layer, m)
        add(metric_layer, d.metric.name)

    # Build node list with stable indices.  Order matters for Sankey
    # readability: real inputs on the left, missing inputs underneath
    # them, metrics on the right.
    all_nodes: List[Tuple[str, str]] = []
    for n in inputs_layer:  all_nodes.append((n, "input"))
    for n in missing_layer: all_nodes.append((n, "missing"))
    for n in metric_layer:  all_nodes.append((n, "metric"))

    idx = {(label, kind): i for i, (label, kind) in enumerate(all_nodes)}

    sources: List[int] = []
    targets: List[int] = []
    values: List[float] = []
    link_labels: List[str] = []
    link_colors: List[str] = []

    for d in deps:
        m_idx = idx[(d.metric.name, "metric")]

        for inp in d.inputs:
            sources.append(idx[(inp, "input")])
            targets.append(m_idx)
            values.append(1)
            link_labels.append(f"{inp} -> {d.metric.name}")
            link_colors.append("rgba(127,179,213,0.45)")

        if show_missing:
            for miss in d.missing_tokens:
                sources.append(idx[(miss, "missing")])
                targets.append(m_idx)
                values.append(1)
                link_labels.append(f"MISSING: {miss}")
                link_colors.append("rgba(231,76,60,0.55)")

    # Aggregate duplicate links so the diagram isn't crushed.
    agg: Dict[Tuple[int, int], Tuple[float, str, str]] = {}
    for s, t, v, lbl, col in zip(sources, targets, values, link_labels, link_colors):
        key = (s, t)
        if key in agg:
            agg[key] = (agg[key][0] + v, agg[key][1], col)
        else:
            agg[key] = (v, lbl, col)
    sources = [k[0] for k in agg]
    targets = [k[1] for k in agg]
    values  = [v for v, _, _ in agg.values()]
    link_labels = [lbl for _, lbl, _ in agg.values()]
    link_colors = [col for _, _, col in agg.values()]

    labels = [n[0] for n in all_nodes]
    colors = [NODE_PALETTE[n[1]] for n in all_nodes]

    fig = go.Figure(data=[go.Sankey(
        arrangement="snap",
        node=dict(
            pad=14,
            thickness=18,
            line=dict(color="rgba(0,0,0,0.4)", width=0.5),
            label=labels,
            color=colors,
            customdata=[n[1] for n in all_nodes],
            hovertemplate="%{label}<br><i>%{customdata}</i><extra></extra>",
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            label=link_labels,
            color=link_colors,
            hovertemplate="%{label}<extra></extra>",
        ),
    )])

    height = max(550, 22 * (len(inputs_layer) + len(missing_layer) + len(metric_layer)))
    fig.update_layout(
        height=min(height, 1400),
        margin=dict(l=10, r=10, t=30, b=10),
        font=dict(size=12),
    )
    return fig


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="APC Formulas Explorer",
        page_icon="🏭",
        layout="wide",
    )

    st.title("APC Formulas Explorer")
    st.caption(
        "Interactive view of how warehouse inputs feed each capacity metric. "
        "Pick a warehouse, narrow by process, and choose a metric to see its "
        "formula tree and any inputs missing from the Inputs sheet."
    )

    # ---- Sidebar -----------------------------------------------------------
    with st.sidebar:
        st.header("Data source")
        xlsx_str = st.text_input("Workbook path", value=str(XLSX_DEFAULT))
        xlsx_path = Path(xlsx_str)
        if not xlsx_path.exists():
            st.error(f"File not found: {xlsx_path}")
            st.stop()

        try:
            book, deps_by_wh = load_workbook(str(xlsx_path))
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            st.exception(exc)
            st.stop()

        st.success(f"Loaded {len(book.inputs)} inputs · {len(book.warehouses)} warehouse(s)")

        st.header("Filters")
        wh = st.selectbox("Warehouse", book.warehouses, index=0)
        deps_all = deps_by_wh[wh]

        main_options = sorted({d.main_process for d in deps_all},
                              key=lambda n: MAIN_PROCESS_ORDER.index(n) if n in MAIN_PROCESS_ORDER else 99)
        main_filter = st.multiselect(
            "Main Process", main_options, default=main_options
        )

        # core options depend on main filter
        deps_main = [d for d in deps_all if d.main_process in main_filter]
        core_options = sorted({d.core_process for d in deps_main},
                              key=lambda n: CORE_PROCESS_ORDER.index(n) if n in CORE_PROCESS_ORDER else 99)
        core_filter = st.multiselect(
            "Core Process", core_options, default=core_options
        )

        deps_filtered = [
            d for d in deps_all
            if d.main_process in main_filter and d.core_process in core_filter
        ]

        metric_names = ["(All metrics)"] + [d.metric.name for d in deps_filtered]
        selected_metric = st.selectbox("Metric", metric_names, index=0)

        show_missing = st.checkbox(
            "Highlight inputs not on the Inputs sheet", value=True,
            help="Tokens used inside formulas (e.g. ML1PPB rate, MPPB UPB, "
                 "Absheer volume) that are not present in the Inputs sheet "
                 "are drawn in red."
        )

        st.divider()
        st.markdown(
            "**Legend**\n\n"
            "- :blue[Input] (Inputs sheet)\n"
            "- :red[Missing input] (referenced in formula only)\n"
            "- :violet[Metric]"
        )

    # ---- Main canvas -------------------------------------------------------
    tabs = st.tabs([
        "Sankey diagram", "Metric details", "Calculator",
        "All metrics", "Inputs sheet",
    ])

    # ---------- Sankey -------------------------------------------------------
    with tabs[0]:
        if not deps_filtered:
            st.info("No metrics match the current filters.")
        else:
            fig = build_sankey(deps_filtered, selected_metric, show_missing)
            st.plotly_chart(fig, use_container_width=True, theme=None)

            n_total = len(deps_filtered)
            n_missing = sum(1 for d in deps_filtered if d.missing_tokens)
            cols = st.columns(4)
            cols[0].metric("Metrics shown", n_total)
            cols[1].metric("With known inputs", sum(1 for d in deps_filtered if d.inputs))
            cols[2].metric("With missing inputs", n_missing)
            cols[3].metric("Inputs in flow",
                           len({i for d in deps_filtered for i in d.inputs}))

    # ---------- Metric details ----------------------------------------------
    with tabs[1]:
        focus = [d for d in deps_filtered if (
            selected_metric == "(All metrics)" or d.metric.name == selected_metric
        )]
        if selected_metric == "(All metrics)":
            st.info("Select a specific metric in the sidebar to see its full "
                    "formula hierarchy.")
        for d in focus[:1] if selected_metric != "(All metrics)" else focus[:5]:
            render_metric_card(d)

    # ---------- Calculator --------------------------------------------------
    with tabs[2]:
        render_calculator(deps_filtered, selected_metric)

    # ---------- All metrics table -------------------------------------------
    with tabs[3]:
        rows = []
        for d in deps_filtered:
            rows.append({
                "Metric": d.metric.name,
                "Main Process": d.main_process,
                "Core Process": d.core_process,
                "Formula": d.metric.primary_formula,
                "# Inputs found": len(d.inputs),
                "# Missing inputs": len(d.missing_tokens),
                "Inputs": ", ".join(d.inputs),
                "Missing": "; ".join(d.missing_tokens),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ---------- Inputs sheet ------------------------------------------------
    with tabs[4]:
        df_in = pd.DataFrame([
            {"Section": i.section, "Input": i.name, "Qualifier": i.qualifier, "Row": i.row}
            for i in book.inputs
        ])
        st.dataframe(df_in, use_container_width=True, hide_index=True)


def render_metric_card(d: MetricDeps) -> None:
    st.subheader(d.metric.name)
    cols = st.columns(3)
    cols[0].markdown(f"**Main Process**\n\n{d.main_process}")
    cols[1].markdown(f"**Core Process**\n\n{d.core_process}")
    cols[2].markdown(f"**Sheet / Row**\n\n{d.metric.sheet} : {d.metric.row}")

    st.markdown("**Primary formula**")
    st.code(d.metric.primary_formula or "(blank)", language="text")

    if d.metric.layers:
        with st.expander("Formula hierarchy (layers down)", expanded=True):
            for i, layer in enumerate(d.metric.layers, start=1):
                st.markdown(f"- **Layer {i}** : {layer}")

    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Inputs (resolved on Inputs sheet)**")
        if d.inputs:
            for inp in d.inputs:
                st.markdown(f"- ✅ {inp}")
        else:
            st.markdown("_None resolved._")

    with cB:
        st.markdown("**Missing inputs (used in formula but not on Inputs sheet)**")
        if d.missing_tokens:
            for tok in d.missing_tokens:
                st.markdown(f"- ⚠️ {tok}")
            st.warning(
                "These tokens are used inside the formula but no matching row "
                "exists on the Inputs sheet. Add them to capture the full "
                "dependency graph."
            )
        else:
            st.markdown("_None — all referenced inputs are on the Inputs sheet._")

    with st.expander("Try a quick calculation", expanded=False):
        render_calculator_for_metric(d, key_prefix=f"card_{d.metric.row}")


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

# Default seed values used the first time a variable is rendered.  These
# only matter for first-paint; the user can overwrite anything.
def _default_value(var: str) -> float:
    name = var.lower()
    if "%" in name:
        return 50.0  # 50% by default
    if "rate" in name:
        return 100.0
    if "volume" in name or "vol" in name.split():
        return 1000.0
    if "upt" in name or "unit per" in name or "units per" in name or "upb" in name:
        return 50.0
    if "cycle" in name or "time" in name:
        return 1.0
    if "buffer" in name:
        return 1.0
    return 1.0


def render_calculator_for_metric(d: MetricDeps, key_prefix: str) -> None:
    """Render number inputs for every variable in the metric's formula and
    show the evaluated result.

    Works for *any* metric — variables are pulled directly from the formula
    string, so missing inputs (those not on the Inputs sheet) get a number
    box too.
    """

    formula = d.metric.primary_formula or ""
    if not formula:
        st.info("This metric has no primary formula text to calculate.")
        return

    variables = extract_variables(formula)

    # Expose layered formulas as additional rows so the user can play with
    # the sub-expressions too.
    layered = [
        layer for layer in d.metric.layers
        if layer and any(op in layer for op in "*/+()")
    ]

    if not variables:
        st.info(
            "Couldn't extract numeric variables from this formula. The "
            "formula text below is descriptive rather than arithmetic."
        )
        st.code(formula, language="text")
        return

    st.caption("Edit any value, then read the result below. Percent values "
               "may be entered as `12` for 12% or `0.12` — both are accepted "
               "and scaled when the variable name contains `%`.")

    # Build a key per (metric, variable) so values persist across reruns.
    values: Dict[str, float] = {}
    grid = st.columns(2)
    for i, var in enumerate(variables):
        with grid[i % 2]:
            label = var
            badge = ""
            # Mark whether the variable resolves to a known input.
            if any(_normalized_eq(var, inp) for inp in d.inputs):
                badge = " ✅"
            elif any(_normalized_eq(var, miss.split(" (")[0]) for miss in d.missing_tokens):
                badge = " ⚠️ missing"
            seed = _default_value(var)
            # If the var name has "%", let the user enter the human value
            # (e.g., 20 means 20%).  We then convert back below.
            if "%" in var:
                v_pct = st.number_input(
                    f"{label}{badge} (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(seed),
                    step=1.0,
                    key=f"{key_prefix}_{i}",
                )
                values[var] = v_pct / 100.0
            else:
                values[var] = st.number_input(
                    f"{label}{badge}",
                    value=float(seed),
                    step=1.0,
                    key=f"{key_prefix}_{i}",
                    format="%.4f",
                )

    # Evaluate primary formula
    result, msg = evaluate(formula, values)
    st.divider()
    st.markdown("**Primary formula result**")
    if result is not None:
        st.metric(label=d.metric.name, value=f"{result:,.4f}")
        st.caption(msg)
    else:
        st.warning(msg)

    with st.expander("Substituted formula", expanded=False):
        st.code(_render_substitution(formula, values), language="text")

    # If layered sub-expressions exist, evaluate each one independently so
    # the user can see partial sums / sub-totals.
    if layered:
        with st.expander("Sub-expressions from formula hierarchy", expanded=False):
            for j, layer in enumerate(layered, start=1):
                # Re-extract variables for this layer; merge with the main
                # values dict (existing inputs reuse their numbers, new
                # ones default to seed value).
                layer_vars = extract_variables(layer)
                layer_vals = dict(values)
                missing_vars = [v for v in layer_vars if v not in values]
                for v in missing_vars:
                    layer_vals[v] = _default_value(v)
                lr, lmsg = evaluate(layer, layer_vals)
                st.markdown(f"**Layer {j}**")
                st.code(layer, language="text")
                if lr is not None:
                    st.write(f"= **{lr:,.4f}**  ({lmsg})")
                else:
                    st.write(f"_{lmsg}_")


def render_calculator(deps_filtered: List[MetricDeps], selected_metric: str) -> None:
    if not deps_filtered:
        st.info("No metrics match the current filters.")
        return

    if selected_metric == "(All metrics)":
        st.info(
            "Pick a specific metric from the **Metric** drop-down in the "
            "sidebar to load its calculator. Below is a quick chooser."
        )
        names = [d.metric.name for d in deps_filtered]
        chosen = st.selectbox(
            "Metric to test", names, key="calc_picker"
        )
        d = next(d for d in deps_filtered if d.metric.name == chosen)
    else:
        match = [d for d in deps_filtered if d.metric.name == selected_metric]
        if not match:
            st.warning("Selected metric is filtered out — adjust filters.")
            return
        d = match[0]

    st.subheader(f"Calculator · {d.metric.name}")
    cols = st.columns(3)
    cols[0].markdown(f"**Main**\n\n{d.main_process}")
    cols[1].markdown(f"**Core**\n\n{d.core_process}")
    cols[2].markdown(f"**Inputs / missing**\n\n"
                     f"{len(d.inputs)} resolved, {len(d.missing_tokens)} missing")

    st.code(d.metric.primary_formula or "(no formula)", language="text")

    render_calculator_for_metric(d, key_prefix=f"calc_{d.metric.row}")


def _normalized_eq(a: str, b: str) -> bool:
    """Loose equality for matching formula tokens to canonical names."""
    return a.strip().lower().replace(" ", "") == b.strip().lower().replace(" ", "")


def _render_substitution(formula: str, values: Dict[str, float]) -> str:
    """Return the formula with each variable replaced by its number, for
    display only (uses the same substitution order as the evaluator)."""
    import re as _re
    out = formula
    for var in sorted(values, key=lambda k: -len(k)):
        v = values[var]
        out = _re.sub(_re.escape(var), f"({v:g})", out, flags=_re.IGNORECASE)
    return out


if __name__ == "__main__":
    main()
