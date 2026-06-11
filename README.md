# APC Formulas Explorer

Interactive web app that visualises how warehouse inputs feed each capacity
metric defined in `APC dashboard Formulas Rev NC.xlsx`.

The app gives Process Engineering a quick way to:

1. Pick a **warehouse** (currently `RUH8`; the parser auto-detects any
   sheet named `Formulas <FC>` so additional warehouses just need a sheet).
2. Filter by **Main Process** (Inbound, Outbound, C-Return, TSI, TSO,
   V-Return, IXD - Cross Transshipment Dock) and **Core Process** (Receive,
   Stow, Pick, Sort, Pack, Receive Dock, Transfer In Stow, C-Return Stow,
   V-Return Pick, Transfer Out Pick, Other).
3. Select a single **metric** to inspect its formula tree (`primary_formula`
   + the "layers down" explanations) and the inputs it consumes.
4. See **missing inputs** flagged in red - tokens that appear inside formulas
   (`ML1PPB rate`, `MPPB UPB`, `Absheer volume`, `UPP CRETs`, ...) but have
   no matching row on the `Inputs` sheet.

## Run

The repo's existing virtual environment under
`capacity_portal/capacity_portal/.venv` already has `streamlit`, `plotly`,
`pandas`, and `openpyxl` installed. From the workspace root:

```
.\capacity_portal\capacity_portal\.venv\Scripts\streamlit.exe run apc_dashboard\app.py
```

Then open the URL Streamlit prints (default <http://localhost:8501>).

If your Excel file lives elsewhere, paste its path into the **Workbook
path** box at the top of the sidebar.

## Files

- `parser.py` - reads `Inputs` and every `Formulas <FC>` sheet into typed
  `InputItem` and `Metric` records, stitching multi-row metrics back
  together.
- `dependencies.py` - matches formula text to canonical inputs via an alias
  dictionary, classifies metrics by Main / Core process, and flags
  formula tokens that aren't on the Inputs sheet.
- `app.py` - Streamlit UI: 4-layer Sankey diagram (Inputs -> Main -> Core ->
  Metric), formula breakdown, and tables for all metrics & inputs.

## Adding a new warehouse

1. Duplicate the `Formulas RUH8` sheet, rename it `Formulas <FC>`.
2. Tweak any FC-specific formulas.
3. Reload the app - the new warehouse appears in the sidebar dropdown.

## Adding / fixing aliases

Edit `ALIAS_TO_CANONICAL` in `dependencies.py` to map a phrase used inside
a formula (e.g. `"hov rate"`) to the exact `name` value on the Inputs sheet
(e.g. `"HOV Large%"`). Add to `KNOWN_MISSING` to surface descriptive
warnings for inputs not present on the Inputs sheet.
