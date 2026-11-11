"""The satin column planner: letters and filled shapes → satin columns, stroke by stroke.

columns      strokes → rails (rays from the centreline to the outline), checks, fonts
autostrokes  an outline's strokes from its medial axis (junctions, corners, free ends)
art_columns  filled artwork shapes (plan_shape) and text (text_columns), flat-work checks
ring_seam    a satin ring's seam rebuilt from its stitches (where a ring starts and its overlap)

Geometry only, in mm with y up; no Ink/Stitch engine calls. Needs the `satin` extra
(numpy, scipy, shapely, fonttools, svgelements): pip install 'cli-anything-inkstitch[satin]'.
"""
