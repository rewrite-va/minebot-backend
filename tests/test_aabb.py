"""Verifies the AABB port against prismarine-physics's real
computeOffsetX/Y/Z semantics: clamp a proposed movement offset down to
stop exactly at contact with a blocking box, only when the two boxes
actually overlap on the other two axes.
"""

from __future__ import annotations

from minebot.physics.aabb import AABB


def test_compute_offset_y_stops_falling_onto_solid_ground():
    ground = AABB(0, 0, 0, 1, 1, 1)  # a solid block from y=0 to y=1
    player = AABB(0, 2, 0, 0.6, 3.8, 0.6)  # standing above it, overlapping x/z

    offset = ground.compute_offset_y(player, -5.0)  # falling fast
    assert offset == -1.0  # clamped so player's bottom (y=2) lands exactly on y=1


def test_compute_offset_y_unaffected_when_not_overlapping_xz():
    ground = AABB(0, 0, 0, 1, 1, 1)
    player = AABB(5, 2, 5, 5.6, 3.8, 5.6)  # far away in x/z

    offset = ground.compute_offset_y(player, -5.0)
    assert offset == -5.0  # unclamped -- no overlap on x/z at all


def test_compute_offset_x_stops_walking_into_a_wall():
    wall = AABB(1, 0, 0, 2, 2, 1)
    player = AABB(0, 0, 0, 0.6, 1.8, 0.6)  # to the west of the wall, overlapping y/z

    offset = wall.compute_offset_x(player, 5.0)  # trying to walk east through it
    assert offset == 0.4  # clamped so player's east face (x=0.6) stops at the wall's face (x=1)


def test_compute_offset_x_negative_direction():
    wall = AABB(-1, 0, 0, 0, 2, 1)
    player = AABB(0.5, 0, 0, 1.1, 1.8, 0.6)

    offset = wall.compute_offset_x(player, -5.0)  # trying to walk west into it
    assert offset == -0.5  # clamped so player's west face (x=0.5) stops at the wall's face (x=0)


def test_compute_offset_z_matches_x_and_y_pattern():
    wall = AABB(0, 0, 1, 1, 2, 2)
    player = AABB(0, 0, 0, 0.6, 1.8, 0.6)

    offset = wall.compute_offset_z(player, 5.0)
    assert offset == 0.4


def test_intersects_true_for_overlapping_boxes():
    a = AABB(0, 0, 0, 1, 1, 1)
    b = AABB(0.5, 0.5, 0.5, 1.5, 1.5, 1.5)
    assert a.intersects(b) is True


def test_intersects_false_for_touching_but_not_overlapping_boxes():
    # Exactly touching at the boundary is NOT an intersection (strict <, >).
    a = AABB(0, 0, 0, 1, 1, 1)
    b = AABB(1, 0, 0, 2, 1, 1)
    assert a.intersects(b) is False


def test_intersects_false_for_disjoint_boxes():
    a = AABB(0, 0, 0, 1, 1, 1)
    b = AABB(5, 5, 5, 6, 6, 6)
    assert a.intersects(b) is False


def test_extend_grows_toward_positive_or_negative_direction():
    box = AABB(0, 0, 0, 1, 1, 1)
    box.extend(2.0, -1.0, 0.5)
    assert (box.min_x, box.max_x) == (0, 3.0)  # positive dx grows maxX
    assert (box.min_y, box.max_y) == (-1.0, 1)  # negative dy grows minY
    assert (box.min_z, box.max_z) == (0, 1.5)


def test_offset_shifts_both_bounds_equally():
    box = AABB(0, 0, 0, 1, 1, 1)
    box.offset(1, 2, 3)
    assert (box.min_x, box.max_x) == (1, 2)
    assert (box.min_y, box.max_y) == (2, 3)
    assert (box.min_z, box.max_z) == (3, 4)
