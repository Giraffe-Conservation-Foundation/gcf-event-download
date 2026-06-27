"""
GCF-specific repeat-group flattening for EarthRanger events.

BACKGROUND
----------
Some GCF event forms contain repeat groups — sections where observers can
record multiple individuals (e.g. each giraffe in a herd sighting). The
EarthRanger API returns these as a mixed parent/child row structure:

  Row 0  — parent:  event metadata (time, id, event_type, …) | no individual data
  Row 1  — child:   individual data (herd member fields)     | no event metadata
  Row 2  — child:   individual data                          | no event metadata
  …

Additionally, some repeat-group fields arrive as a list of dicts inside a
single cell (e.g. Individuals = [{"Age": "ad", "Sex": "m"}, …]). These may
be Python list objects or JSON strings depending on how the EarthRanger API
serialised them.

flatten_gcf_repeat_groups(), registered as a wt task via @register():
  1. Detects the orphan-child pattern and forward-fills event metadata from
     parent rows onto child rows, then drops the now-superseded parent rows.
  2. Detects any column containing list-of-dict data (including JSON strings)
     and explodes each into one row per repeat-group entry, normalising
     sub-fields into flat columns prefixed with the original column name.

Field names of repeat groups vary by EarthRanger instance/event form
customization, so the detection logic is schema-agnostic — it discovers
list-of-dict columns at runtime rather than requiring a fixed set of names.

USAGE IN spec.yaml
-------------------
    requirements:
      - name: ecoscope-workflows-ext-gcf
        version: "0.1.1"
        channel: https://repo.prefix.dev/ecoscope-workflows-gcf/

    workflow:
      - name: Flatten GCF Repeat Groups
        id: flatten_repeat_groups
        task: flatten_gcf_repeat_groups
        partial:
          df: ${{ workflow.convert_event_details_timezone.return }}
"""

import json
from typing import Annotated

import geopandas as gpd
import pandas as pd
from ecoscope.platform.annotations import AnyGeoDataFrame
from pydantic import Field
from wt_registry import register


def _to_list_of_dicts(x) -> list[dict] | None:
    """Return x as a list-of-dicts if possible, else None.

    Handles both native Python lists and JSON-string representations.
    """
    if isinstance(x, list):
        if len(x) > 0 and isinstance(x[0], dict):
            return x
        return None
    if isinstance(x, str):
        try:
            parsed = json.loads(x)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


@register()
def flatten_gcf_repeat_groups(
    df: Annotated[
        AnyGeoDataFrame,
        Field(
            description=(
                "Events GeoDataFrame after normalize_json_column and "
                "drop_column_prefix steps. Expects columns produced by the "
                "standard Ecoscope processing chain: event_datetime, event_id, "
                "serial_number, event_type, etc."
            ),
            exclude=True,
        ),
    ],
) -> Annotated[AnyGeoDataFrame, Field()]:
    """
    Forward-fill parent event metadata onto orphan child rows from repeat
    groups, drop superseded parent rows, and explode list-of-dict columns.

    Events that have no repeat groups pass through unchanged.
    """

    # ── 1. Repair repeat-group "orphan" child rows ────────────────────────────
    # Identify which column to use as the "is this row a proper event?" check.
    # Child rows from repeat groups have empty/NaN values in event-level fields.
    _id_check = next(
        (c for c in ["serial_number", "event_datetime", "event_type"] if c in df.columns),
        None,
    )

    if _id_check:
        _orphan = df[_id_check].apply(
            lambda x: pd.isna(x) or (isinstance(x, str) and x.strip() == "")
        )

        if _orphan.any() and not _orphan.all():
            # Forward-fill scalar event metadata columns from parent → child rows
            _meta_cols = [
                c for c in [
                    "event_datetime", "event_id", "serial_number", "event_type",
                    "priority", "title", "state", "updated_at", "created_at",
                    "is_collection", "reported_by_name", "reported_by_subtype",
                    "longitude", "latitude",
                ]
                if c in df.columns
            ]
            df[_meta_cols] = df[_meta_cols].ffill()

            # Forward-fill geometry separately (not a scalar column)
            _geom = df.geometry.values.copy()
            for _i in range(1, len(_geom)):
                if _geom[_i] is None or (
                    hasattr(_geom[_i], "is_empty") and _geom[_i].is_empty
                ):
                    _geom[_i] = _geom[_i - 1]
            df = df.set_geometry(gpd.GeoSeries(_geom, crs=4326))

            # Drop parent rows that have been superseded by their children.
            # A parent row is identified as a non-orphan followed immediately
            # by an orphan child.
            _parent_mask = (~_orphan) & _orphan.shift(-1, fill_value=False)
            df = gpd.GeoDataFrame(
                df[~_parent_mask].reset_index(drop=True),
                geometry="geometry",
                crs=4326,
            )

    # ── 2. Explode list-of-dict columns ──────────────────────────────────────
    # Detect any column whose cells contain list-of-dicts (either as Python
    # lists or as JSON strings). EarthRanger repeat-group fields such as
    # "Individuals" arrive this way after the normalize/prefix-drop steps.
    list_dict_cols = [
        col
        for col in df.columns
        if df[col].apply(lambda x: _to_list_of_dicts(x) is not None).any()
    ]

    for col in list_dict_cols:
        # Parse JSON strings → Python lists; use [{}] as placeholder for rows
        # that have no data in this repeat group so explode preserves them.
        df[col] = df[col].apply(
            lambda x: _to_list_of_dicts(x) or [{}]
        )
        df = df.explode(col, ignore_index=True)

        # Normalise the dict in each cell into flat columns, prefixed with
        # the original column name to avoid clashes.
        nested = pd.json_normalize(
            df[col].apply(lambda x: x if isinstance(x, dict) else {})
        )
        nested.columns = [f"{col}_{c}" for c in nested.columns]

        df = gpd.GeoDataFrame(
            pd.concat(
                [df.drop(columns=[col]).reset_index(drop=True), nested],
                axis=1,
            ),
            geometry="geometry",
            crs=4326,
        )

    return df
