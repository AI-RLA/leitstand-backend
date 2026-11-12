"""The swath angle the planner chooses when the caller gives none.

Fields2Cover is only installed in the planner image, so these run there:

    docker compose run --rm --no-deps -v "$PWD/coverage-planner:/app:ro" \
        coverage-planner python -m unittest discover -s tests
"""

import math
import unittest

import fields2cover as f2c
from app import _MAX_EDGE_DIRECTIONS, _fewest_swaths, _swath_bearing

WIDTH_M = 3.0


def _cell(points: list[tuple[float, float]]) -> f2c.Cell:
    ring = f2c.LinearRing()
    for x, y in [*points, points[0]]:
        ring.addPoint(f2c.Point(x, y))
    cell = f2c.Cell()
    cell.addRing(ring)
    return cell


def _turned(points: list[tuple[float, float]], bearing_deg: float) -> list[tuple[float, float]]:
    """Return the points turned clockwise, so a side pointing north points along the bearing."""
    a = math.radians(-bearing_deg)
    return [
        (x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)) for x, y in points
    ]


def _with_clicks(points: list[tuple[float, float]], parts: int) -> list[tuple[float, float]]:
    """Return the ring with every side split into equal parts, as a boundary drawn by clicking."""
    out = []
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        out += [(ax + (bx - ax) * i / parts, ay + (by - ay) * i / parts) for i in range(parts)]
    return out


def _fewest_possible(cell: f2c.Cell) -> int:
    return f2c.SG_BruteForce().generateBestSwaths(f2c.OBJ_NSwath(), WIDTH_M, cell).size()


class _CountingGenerator:
    """Count the layouts a search asks Fields2Cover for."""

    def __init__(self) -> None:
        self.calls = 0
        self._generator = f2c.SG_BruteForce()

    def generateSwaths(self, angle: float, width: float, cell: f2c.Cell) -> f2c.Swaths:
        self.calls += 1
        return self._generator.generateSwaths(angle, width, cell)


class SwathAngleTest(unittest.TestCase):
    def assertLaidOn(self, points: list[tuple[float, float]], *bearings_deg: float) -> None:
        cell = _cell(points)
        swaths = _fewest_swaths(f2c.SG_BruteForce(), WIDTH_M, cell)
        self.assertLessEqual(swaths.size(), _fewest_possible(cell))
        bearing = _swath_bearing(swaths.at(0))
        # Modulo 180, so 179.9 and 0.1 count as the same line.
        off = min(
            min(abs(bearing - b) % 180.0, 180.0 - abs(bearing - b) % 180.0) for b in bearings_deg
        )
        self.assertLess(off, 0.1, f"swaths on {bearing:.2f} deg, expected one of {bearings_deg}")

    def test_turned_rectangle_runs_along_its_long_side(self) -> None:
        self.assertLaidOn(_turned([(0, 0), (23, 0), (23, 51), (0, 51)], 8.0), 8.0)

    def test_side_drawn_with_many_clicks_still_counts_as_one_side(self) -> None:
        self.assertLaidOn(_with_clicks(_turned([(0, 0), (23, 0), (23, 51), (0, 51)], 8.0), 7), 8.0)

    def test_parallelogram_runs_along_its_slanted_sides(self) -> None:
        lean = 80 * math.tan(math.radians(25))
        self.assertLaidOn([(0, 0), (30, 0), (30 + lean, 80), (lean, 80)], 25.0)

    def test_trapezoid_runs_along_one_of_its_legs(self) -> None:
        leg_deg = math.degrees(math.atan2(10, 60))
        self.assertLaidOn([(0, 0), (40, 0), (30, 60), (10, 60)], leg_deg, 180.0 - leg_deg)

    def test_lines_start_from_the_same_side_unless_the_other_saves_a_swath(self) -> None:
        cell = _cell(_turned([(0, 0), (23, 0), (23, 51), (0, 51)], 8.0))
        chosen = _fewest_swaths(f2c.SG_BruteForce(), WIDTH_M, cell)
        same_side = f2c.SG_BruteForce().generateSwaths(math.radians(8.0), WIDTH_M, cell)
        start = chosen.at(0).getPath()
        expected = same_side.at(0).getPath()
        self.assertAlmostEqual(start.getX(0), expected.getX(0), places=6)
        self.assertAlmostEqual(start.getY(0), expected.getY(0), places=6)

    def test_concave_field_needs_no_more_swaths_than_fields2cover_finds(self) -> None:
        # A bearing and its opposite lay the lines from opposite sides, and on this field the two
        # differ by a swath.
        points = [
            (49.7, 16.7), (19.1, 40.0), (2.7, 53.8), (-48.4, 16.4), (-45.3, -3.2),
            (-50.0, -25.1), (1.3, -51.6), (15.2, -25.7), (21.9, -19.9),
        ]  # fmt: skip
        cell = _cell(points)
        swaths = _fewest_swaths(f2c.SG_BruteForce(), WIDTH_M, cell)
        self.assertEqual(swaths.size(), 27)

    def test_boundary_with_many_short_sides_tries_a_bounded_number_of_directions(self) -> None:
        circle = [
            (60 * math.cos(math.radians(a)), 60 * math.sin(math.radians(a))) for a in range(360)
        ]
        generator = _CountingGenerator()
        _fewest_swaths(generator, WIDTH_M, _cell(circle))
        self.assertLessEqual(generator.calls, 2 * (180 + _MAX_EDGE_DIRECTIONS) + 1)

    def test_l_shape_runs_along_its_long_arm(self) -> None:
        self.assertLaidOn([(0, 0), (100, 0), (100, 20), (20, 20), (20, 80), (0, 80)], 90.0)


if __name__ == "__main__":
    unittest.main()
