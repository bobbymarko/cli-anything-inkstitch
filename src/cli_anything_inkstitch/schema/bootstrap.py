"""Hardcoded minimal stitch-type schema.

This is the v0.1 fallback used when no extracted schema cache exists.
SPEC.md §3 describes the install-time extractor that should replace this with
the full schema mined from inkstitch's element classes + INX templates.

Param values here come from inkstitch.org/namespace/ and the brief — they
are *correct for the documented set* but not exhaustive (the live element
classes have many more params).
"""

from __future__ import annotations

# Each stitch type has:
#   defining_attribute: the inkstitch:* attr that "marks" this stitch type
#                       (None means any unmarked element with matching geometry)
#   geometry_requirements: list of strings describing what the SVG element needs
#   params: dict of param_name -> {type, default, min, max, unit, gui_text, description, enum?}

_FLOAT = "float"
_BOOL = "boolean"
_INT = "int"
_STR = "string"

STITCH_TYPES: dict[str, dict] = {
    "auto_fill": {
        "defining_attribute": {"name": "auto_fill", "value": "True"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "auto_fill":             {"type": _BOOL,  "default": True},
            "angle":                 {"type": _FLOAT, "default": 0.0,   "unit": "deg",
                                       "gui_text": "Angle of lines of stitches"},
            "expand_mm":             {"type": _FLOAT, "default": 0.0,   "unit": "mm",
                                       "gui_text": "Expand"},
            "max_stitch_length_mm":  {"type": _FLOAT, "default": 3.0,   "unit": "mm", "min": 0.1, "max": 10.0,
                                       "gui_text": "Maximum fill stitch length"},
            "row_spacing_mm":        {"type": _FLOAT, "default": 0.25,  "unit": "mm", "min": 0.05, "max": 5.0,
                                       "gui_text": "Spacing between rows"},
            "running_stitch_length_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm",
                                       "gui_text": "Running stitch length"},
            "staggers":              {"type": _INT,   "default": 4,     "min": 1, "max": 20,
                                       "gui_text": "Stagger rows this many times before repeating"},
            "fill_underlay":         {"type": _BOOL,  "default": False, "gui_text": "Fill underlay"},
            "fill_underlay_angle":   {"type": _STR,   "default": "",    "gui_text": "Fill underlay angle (comma-separated for multiple)"},
            "fill_underlay_inset_mm":{"type": _FLOAT, "default": 0.0,   "unit": "mm"},
            "fill_underlay_max_stitch_length_mm": {"type": _FLOAT, "default": 3.0, "unit": "mm"},
            "fill_underlay_row_spacing_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm"},
            "ties":                  {"type": _BOOL,  "default": True,  "gui_text": "Lock stitches"},
            "stroke_first":          {"type": _BOOL,  "default": False},
        },
    },
    "legacy_fill": {
        "defining_attribute": {"name": "auto_fill", "value": "False"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "auto_fill":             {"type": _BOOL,  "default": False},
            "angle":                 {"type": _FLOAT, "default": 0.0, "unit": "deg"},
            "flip":                  {"type": _BOOL,  "default": False},
            "max_stitch_length_mm":  {"type": _FLOAT, "default": 3.0, "unit": "mm"},
            "row_spacing_mm":        {"type": _FLOAT, "default": 0.25, "unit": "mm"},
            "end_row_spacing_mm":    {"type": _FLOAT, "default": 0.25, "unit": "mm"},
            "staggers":              {"type": _INT,   "default": 4, "min": 1, "max": 20},
        },
    },
    "running_stitch": {
        "defining_attribute": None,
        "geometry_requirements": ["stroke"],
        "params": {
            "running_stitch_length_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm", "min": 0.1, "max": 10.0,
                                          "gui_text": "Running stitch length"},
            "repeats":               {"type": _INT,   "default": 1, "min": 1, "max": 20,
                                       "gui_text": "Times to run down and back"},
            "bean_stitch_repeats":   {"type": _INT,   "default": 0, "min": 0, "max": 20},
            "ties":                  {"type": _BOOL,  "default": True},
        },
    },
    "bean_stitch": {
        "defining_attribute": {"name": "stroke_method", "value": "bean_stitch"},
        "geometry_requirements": ["stroke"],
        "params": {
            "stroke_method":         {"type": _STR,   "default": "bean_stitch"},
            "running_stitch_length_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm"},
            "bean_stitch_repeats":   {"type": _INT,   "default": 1, "min": 1, "max": 20},
            "repeats":               {"type": _INT,   "default": 1, "min": 1, "max": 20},
        },
    },
    "zigzag_stitch": {
        "defining_attribute": {"name": "stroke_method", "value": "zigzag_stitch"},
        "geometry_requirements": ["stroke"],
        "params": {
            "stroke_method":         {"type": _STR,   "default": "zigzag_stitch"},
            "satin_column":          {"type": _BOOL,  "default": False},
            "zigzag_spacing_mm":     {"type": _FLOAT, "default": 0.4, "unit": "mm", "min": 0.1, "max": 5.0},
            "running_stitch_length_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm"},
            "repeats":               {"type": _INT,   "default": 1, "min": 1, "max": 20},
        },
    },
    "ripple_stitch": {
        "defining_attribute": {"name": "stroke_method", "value": "ripple_stitch"},
        "geometry_requirements": ["stroke"],
        "params": {
            "stroke_method":         {"type": _STR,   "default": "ripple_stitch"},
            "line_count":            {"type": _INT,   "default": 10, "min": 1, "max": 200},
            "exponent":              {"type": _FLOAT, "default": 1.0, "min": 0.1, "max": 10.0},
            "skip_start":            {"type": _INT,   "default": 0, "min": 0, "max": 100},
            "skip_end":              {"type": _INT,   "default": 0, "min": 0, "max": 100},
        },
    },
    "manual_stitch": {
        "defining_attribute": {"name": "manual_stitch", "value": "True"},
        "geometry_requirements": ["path"],
        "params": {
            "manual_stitch":         {"type": _BOOL,  "default": True},
        },
    },
    "satin_column": {
        "defining_attribute": {"name": "satin_column", "value": "True"},
        "geometry_requirements": ["stroke", "two_rails_with_rungs"],
        "params": {
            "satin_column":          {"type": _BOOL,  "default": True},
            "pull_compensation_mm":  {"type": _FLOAT, "default": 0.0, "unit": "mm", "min": -10.0, "max": 10.0,
                                       "gui_text": "Pull compensation"},
            "zigzag_spacing_mm":     {"type": _FLOAT, "default": 0.4, "unit": "mm", "min": 0.1, "max": 5.0,
                                       "gui_text": "Zig-zag spacing (peak-to-peak)"},
            "center_walk_underlay":  {"type": _BOOL,  "default": False},
            "center_walk_underlay_stitch_length_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm"},
            "contour_underlay":      {"type": _BOOL,  "default": False},
            "contour_underlay_inset_mm": {"type": _FLOAT, "default": 0.4, "unit": "mm"},
            "contour_underlay_stitch_length_mm": {"type": _FLOAT, "default": 1.5, "unit": "mm"},
            "zigzag_underlay":       {"type": _BOOL,  "default": False},
            "zigzag_underlay_inset_mm": {"type": _FLOAT, "default": 0.4, "unit": "mm"},
            "zigzag_underlay_spacing_mm": {"type": _FLOAT, "default": 1.0, "unit": "mm"},
            "ties":                  {"type": _BOOL,  "default": True},
        },
    },
    "e_stitch": {
        "defining_attribute": {"name": "satin_method", "value": "e_stitch"},
        "geometry_requirements": ["stroke", "two_rails_with_rungs"],
        "params": {
            "satin_column":          {"type": _BOOL,  "default": True},
            "satin_method":          {"type": _STR,   "default": "e_stitch"},
            "zigzag_spacing_mm":     {"type": _FLOAT, "default": 1.0, "unit": "mm"},
            "pull_compensation_mm":  {"type": _FLOAT, "default": 0.0, "unit": "mm"},
        },
    },
    "contour_fill": {
        "defining_attribute": {"name": "fill_method", "value": "contour_fill"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "contour_fill"},
            "row_spacing_mm":        {"type": _FLOAT, "default": 0.25, "unit": "mm"},
            "max_stitch_length_mm":  {"type": _FLOAT, "default": 3.0, "unit": "mm"},
            "avoid_self_crossing":   {"type": _BOOL,  "default": True},
            "smoothness_mm":         {"type": _FLOAT, "default": 0.0, "unit": "mm"},
        },
    },
    "guided_fill": {
        "defining_attribute": {"name": "fill_method", "value": "guided_fill"},
        "geometry_requirements": ["closed_filled_path", "guide_line"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "guided_fill"},
            "row_spacing_mm":        {"type": _FLOAT, "default": 0.25, "unit": "mm"},
            "max_stitch_length_mm":  {"type": _FLOAT, "default": 3.0, "unit": "mm"},
            "guided_fill_strategy":  {"type": _STR,   "default": "copy", "enum": ["copy", "parallel_offset"]},
        },
    },
    "meander_fill": {
        "defining_attribute": {"name": "fill_method", "value": "meander_fill"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "meander_fill"},
            "meander_pattern":       {"type": _STR,   "default": "scribble"},
            "meander_scale_percent": {"type": _FLOAT, "default": 100.0, "unit": "%"},
            "meander_angle":         {"type": _FLOAT, "default": 0.0, "unit": "deg"},
        },
    },
    "circular_fill": {
        "defining_attribute": {"name": "fill_method", "value": "circular_fill"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "circular_fill"},
            "row_spacing_mm":        {"type": _FLOAT, "default": 0.25, "unit": "mm"},
            "max_stitch_length_mm":  {"type": _FLOAT, "default": 3.0, "unit": "mm"},
            "clockwise":             {"type": _BOOL,  "default": True},
        },
    },
    "tartan_fill": {
        "defining_attribute": {"name": "fill_method", "value": "tartan_fill"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "tartan_fill"},
            "tartan_angle":          {"type": _FLOAT, "default": 0.0, "unit": "deg"},
            "herringbone_width_mm":  {"type": _FLOAT, "default": 0.0, "unit": "mm"},
        },
    },
    "linear_gradient_fill": {
        "defining_attribute": {"name": "fill_method", "value": "linear_gradient_fill"},
        "geometry_requirements": ["closed_filled_path", "linear_gradient"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "linear_gradient_fill"},
            "row_spacing_mm":        {"type": _FLOAT, "default": 0.25, "unit": "mm"},
            "max_stitch_length_mm":  {"type": _FLOAT, "default": 3.0, "unit": "mm"},
        },
    },
    "cross_stitch": {
        "defining_attribute": {"name": "fill_method", "value": "cross_stitch"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "cross_stitch"},
            "pattern_size_mm":       {"type": _FLOAT, "default": 2.0, "unit": "mm"},
            "fill_coverage":         {"type": _FLOAT, "default": 1.0, "min": 0.1, "max": 1.0},
            "max_cross_stitch_length_mm": {"type": _FLOAT, "default": 4.0, "unit": "mm"},
        },
    },
    "cross_stitch_half": {
        "defining_attribute": {"name": "fill_method", "value": "cross_stitch_half"},
        "geometry_requirements": ["closed_filled_path"],
        "params": {
            "fill_method":           {"type": _STR,   "default": "cross_stitch_half"},
            "pattern_size_mm":       {"type": _FLOAT, "default": 2.0, "unit": "mm"},
        },
    },
}


VISUAL_COMMANDS: list[dict] = [
    {"id": "inkstitch_stop",        "name": "stop",         "scope": "element",
     "description": "Stop the machine after this element finishes."},
    {"id": "inkstitch_trim",        "name": "trim",         "scope": "element",
     "description": "Trim the thread after this element."},
    {"id": "inkstitch_ignore",      "name": "ignore",       "scope": "element",
     "description": "Skip this element entirely during stitch generation."},
    {"id": "inkstitch_fill_start",  "name": "fill_start",   "scope": "element",
     "description": "Mark the start position for fill stitching."},
    {"id": "inkstitch_fill_end",    "name": "fill_end",     "scope": "element",
     "description": "Mark the end position for fill stitching."},
    {"id": "inkstitch_pause",       "name": "pause",        "scope": "element",
     "description": "Pause the machine after this element."},
    {"id": "inkstitch_satin_start", "name": "satin_start",  "scope": "element",
     "description": "Mark the start position for a satin column."},
    {"id": "inkstitch_satin_end",   "name": "satin_end",    "scope": "element",
     "description": "Mark the end position for a satin column."},
]


def bootstrap_schema(version: str = "bootstrap") -> dict:
    return {
        "inkstitch_version": version,
        "extracted_at": None,
        "namespace": "http://inkstitch.org/namespace",
        "stitch_types": STITCH_TYPES,
        "extensions": {},  # populated only by full extractor
        "commands": VISUAL_COMMANDS,
        "machine_formats": [],  # populated by export.formats at runtime
    }
