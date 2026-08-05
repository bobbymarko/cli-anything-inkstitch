"""Stitch-density analysis: penetrations per mm², binned on a grid.

Density is the failure mode previews hide best: every layer looks fine
alone, but satin over tatami over underlay stacks needle penetrations
until the fabric cuts, the needle deflects, or the patch turns to
cardboard. The numbers come from the ENGINE's stitch plan (every vertex
is a needle penetration — design_model.extract_stitch_blocks), so this
measures what the machine will actually sew, not what the vectors imply.

Thresholds are calibrated against physical evidence, not just rules of
thumb: the celtic 5in patch stitched out clean with 32/mm² peaks where
three deliberate layers stack (satin over satin over tatami at the
spiral), so peaks in that range are "review", not "failure". Defaults:
warn 20/mm² (worth a look), error 35/mm² (beyond anything a validated
sew-out has carried — needle cutting / thread shredding territory).
Satin RAILS concentrate penetrations linearly, so small hot cells at
column edges are normal; a region only matters when it has area — 
warnings need >=1mm², errors flag at any size. Both thresholds are
configurable per call.
"""

from __future__ import annotations

from dataclasses import dataclass

PX_PER_MM = 96.0 / 25.4


@dataclass
class DensityGrid:
    cell_mm: float
    x0_mm: float
    y0_mm: float
    cols: int
    rows: int
    counts: list[list[int]]           # [row][col] penetrations

    def density(self, r: int, c: int) -> float:
        return self.counts[r][c] / (self.cell_mm * self.cell_mm)

    def peak(self) -> float:
        m = max((max(row) for row in self.counts), default=0)
        return m / (self.cell_mm * self.cell_mm)


def density_grid(points_px, *, cell_mm: float = 0.5) -> DensityGrid | None:
    """Bin needle penetrations (px coords) into cell_mm × cell_mm cells."""
    if not points_px:
        return None
    xs = [p[0] / PX_PER_MM for p in points_px]
    ys = [p[1] / PX_PER_MM for p in points_px]
    x0, y0 = min(xs), min(ys)
    cols = int((max(xs) - x0) / cell_mm) + 1
    rows = int((max(ys) - y0) / cell_mm) + 1
    counts = [[0] * cols for _ in range(rows)]
    for x, y in zip(xs, ys):
        counts[int((y - y0) / cell_mm)][int((x - x0) / cell_mm)] += 1
    return DensityGrid(cell_mm, x0, y0, cols, rows, counts)


def hotspots(grid: DensityGrid, *, warn: float = 20.0,
             error: float = 35.0) -> list[dict]:
    """Connected regions of cells at/above `warn`, worst first.

    Single isolated warn-level cells are noise (a satin end bar-tack,
    a rail edge); a warning region must reach 1mm² of area, an
    error-level peak flags at any size.
    """
    over = {(r, c)
            for r in range(grid.rows) for c in range(grid.cols)
            if grid.density(r, c) >= warn}
    seen: set[tuple[int, int]] = set()
    regions = []
    for start in over:
        if start in seen:
            continue
        stack, cells = [start], []
        seen.add(start)
        while stack:
            r, c = stack.pop()
            cells.append((r, c))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    n = (r + dr, c + dc)
                    if n in over and n not in seen:
                        seen.add(n)
                        stack.append(n)
        peak = max(grid.density(r, c) for r, c in cells)
        area = len(cells) * grid.cell_mm ** 2
        if peak < error and area < 1.0:
            continue
        rs = [r for r, _ in cells]
        cs = [c for _, c in cells]
        regions.append({
            "severity": "error" if peak >= error else "warning",
            "peak_per_mm2": round(peak, 1),
            "area_mm2": round(area, 2),
            "x_mm": round(grid.x0_mm + min(cs) * grid.cell_mm, 2),
            "y_mm": round(grid.y0_mm + min(rs) * grid.cell_mm, 2),
            "w_mm": round((max(cs) - min(cs) + 1) * grid.cell_mm, 2),
            "h_mm": round((max(rs) - min(rs) + 1) * grid.cell_mm, 2),
        })
    regions.sort(key=lambda r: -r["peak_per_mm2"])
    return regions


def render_heatmap(grid: DensityGrid, out_path: str, *, warn: float = 20.0,
                   error: float = 35.0, scale: int = 8) -> str:
    """PNG heat ramp: transparent → green → yellow → red at `error`+."""
    from PIL import Image
    img = Image.new("RGBA", (grid.cols, grid.rows), (0, 0, 0, 0))
    px = img.load()
    for r in range(grid.rows):
        for c in range(grid.cols):
            d = grid.density(r, c)
            if d <= 0:
                continue
            t = min(d / error, 1.0)
            if d >= error:
                color = (220, 30, 30, 230)
            elif d >= warn:
                f = (d - warn) / max(error - warn, 1e-9)
                color = (230, int(200 - 140 * f), 30, 210)
            else:
                f = t
                color = (int(60 + 170 * f), 200, 60, int(60 + 120 * f))
            px[c, r] = color
    img = img.resize((grid.cols * scale, grid.rows * scale), Image.NEAREST)
    img.save(out_path)
    return out_path
