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
single cell (e.g. detail_Herd = [{"age": "adult", "sex": "female"}, …]).

This module provides flatten_gcf_repeat_groups(), registered as a wt task,
which:
  1. Detects the orphan-child pattern and forward-fills event metadata from
     parent rows onto child rows, then drops the now-superseded parent rows.
  2. Detects list-of-dict columns (detail_* prefix) and explodes them into
     one row per repeat-group entry, normalising sub-fields into flat columns.

PACKAGING
---------
This function lives in the ecoscope-workflows-ext-gcf package
(https://github.com/Giraffe-Conservation-Foundation/ecoscope-workflows-ext-gcf),
which any GCF module's spec.yaml can pull straight from GitHub as a PyPI-style
git requirement — no conda channel or publishing step required:

    requirements:
      - name: ecoscope-workflows-ext-gcf
        git: https://github.com/Giraffe-Conservation-Foundation/ecoscope-workflows-ext-gcf.git
        tag: v0.1.0

    workflow:
      - name: Flatten GCF Repeat Groups
        id: flatten_repeat_groups
        task: flatten_gcf_repeat_groups
        partial:
          df: ${{ workflow.convert_event_details_timezone.return }}

The compiled workflow will call flatten_gcf_repeat_groups(df=...) and pass
the result to the next task.
"""

from typing import Annotated

import geopandas as gpd
import pandas as pd
from ecoscope.platform.annotations import AnyGeoDataFrame
from pydantic import Field
from wt_registry import register


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

    # ── 2. Explode list-of-dict columns (e.g. detail_Herd) ───────────────────
    # These are repeat-group fields where the API returns a list of dicts
    # (one dict per sub-observation). We explode each such column so that
    # every sub-observation becomes its own row, then normalise the dict
    # keys into flat columns.
    list_dict_cols = [
        col
        for col in df.columns
        if col.startswith("detail_")
        and df[col]
        .apply(lambda x: isinstance(x, list) and len(x) > 0 and isinstance(x[0], dict))
        .any()
    ]

    for col in list_dict_cols:
        # Ensure every cell has at least one entry so explode preserves rows
        # for events that don't have data in this repeat group.
        df[col] = df[col].apply(
            lambda x: x if (isinstance(x, list) and len(x) > 0) else [{}]
        )
        df = df.explode(col, ignore_index=True)

        # Normalise the dict in each cell into flat columns, prefixed with
        # the original column name to avoid clashes.
        nested = pd.json_normalize(
            df[col].apply(lambda x: x if isinstance(x, dict) else {})
        )
        # Prefix nested columns to avoid name collisions
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
