# GCF Event Download — Ecoscope Workflow

Ecoscope Desktop workflow that replicates the **Twiga Tools Event Download page**
(`24_📥_Event_download.py`). Downloads, flattens, and exports EarthRanger events
to CSV or Parquet by event type and date range.

---

## Files

| File | Purpose |
|------|---------|
| `spec.yaml` | Main workflow definition — tasks, parameters, UI form |
| `pixi.toml` | Build dependencies (for compiling the workflow) |
| `layout.json` | Dashboard widget layout |
| `custom_tasks/flatten_repeat_groups.py` | GCF-specific repeat-group logic (see below) |

---

## Loading into Ecoscope Desktop

1. Open **Ecoscope Desktop**
2. Go to **Workflow Templates → + Add Template**
3. Paste the URL of this repo (once published to GitHub)
4. The template appears in your available templates list

---

## What this workflow does

| Step | What happens |
|------|-------------|
| Time Range + Data Source | Pick date range, timezone, and your EarthRanger connection |
| Get Event Data | Fetches events with `include_details=True` and `include_display_values=True` — coded values are automatically resolved to human-readable titles |
| Process Event Details | Normalises `event_details` JSON into flat columns (replaces the Streamlit `pd.json_normalize` step) |
| Reported By | Extracts `reported_by_name` and `reported_by_subtype` into their own columns |
| Column cleanup | Renames `id → event_id`, `time → event_datetime`; drops internal/system columns (configurable) |
| Optional SQL filter | Filter or transform the data with a SQL query before export |
| Group Data | Optionally split output by time period or event category |
| Persist | Exports to CSV and/or Parquet in the Ecoscope results folder |

---

## GCF repeat-group handling (Option B)

The Streamlit page includes custom logic for GCF event types that use
EarthRanger's **repeat groups** (e.g. recording each giraffe in a herd
individually). The ER API returns these as orphan child rows with no event
metadata — the Streamlit page forward-fills metadata and explodes list-of-dict
columns.

**This logic is in `custom_tasks/flatten_repeat_groups.py`** but is currently
commented out in `spec.yaml`. To activate it:

1. The function needs to be **packaged as an Ecoscope workflow extension**
   (e.g. `ecoscope-workflows-ext-gcf`) and published to a prefix.dev channel.
2. Add the requirement to `spec.yaml` and uncomment the task block (clearly
   marked with `# GCF CUSTOM TASK` comments).

Contact the **wildlife-dynamics team** to discuss packaging. In the meantime,
events without repeat groups export correctly; repeat-group events export one
row per event rather than one row per group entry.

---

## Logo / branding

**Ecoscope Desktop does not currently support a custom logo in workflow
definitions.** The `workflow_details` task sets the workflow name and
description only. Ask the wildlife-dynamics team whether a branding override
is planned for a future Ecoscope Desktop release.

---

## Column differences vs. Streamlit page

| Streamlit page | This workflow | Notes |
|----------------|---------------|-------|
| UUID resolution for detail_ columns | `include_display_values=True` | Ecoscope handles this natively — no custom lookup needed |
| `detail_` prefix on event detail columns | `event_details__` prefix → stripped by `drop_column_prefix` | Same result, different mechanism |
| `id` → `event_id` | Handled by `map_columns` rename | ✅ |
| `time` → `event_datetime` | Handled by `map_columns` rename | ✅ |
| Repeat-group orphan rows | `custom_tasks/flatten_repeat_groups.py` | Needs packaging — see above |

---

## Requirements

- Ecoscope Desktop with a configured EarthRanger data source
- `ecoscope-platform >= 2.11.14, < 2.12.0`
- `ecoscope-workflows-ext-custom 0.1.0rc6`
