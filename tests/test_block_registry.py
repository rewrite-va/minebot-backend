from minebot.protocol.block_registry import BLOCK_REGISTRY


def test_air_is_air_and_not_solid():
    info = BLOCK_REGISTRY.get(0)
    assert info is not None
    assert info.name == "minecraft:air"
    assert info.air is True
    assert BLOCK_REGISTRY.is_air(0) is True
    assert BLOCK_REGISTRY.is_solid(0) is False


def test_stone_is_solid_and_not_air():
    info = BLOCK_REGISTRY.get(1)
    assert info is not None
    assert info.name == "minecraft:stone"
    assert BLOCK_REGISTRY.is_solid(1) is True
    assert BLOCK_REGISTRY.is_air(1) is False
    assert BLOCK_REGISTRY.is_liquid(1) is False


def test_unknown_state_id_treated_as_air_not_solid():
    huge_id = 10_000_000
    assert BLOCK_REGISTRY.get(huge_id) is None
    assert BLOCK_REGISTRY.is_air(huge_id) is True
    assert BLOCK_REGISTRY.is_solid(huge_id) is False


def test_water_is_liquid_and_not_solid():
    water_states = BLOCK_REGISTRY.states_named("minecraft:water")
    assert water_states
    for info in water_states:
        assert info.liquid is True
        assert info.solid is False


def test_ladder_is_climbable():
    ladder_states = BLOCK_REGISTRY.states_named("minecraft:ladder")
    assert ladder_states
    for info in ladder_states:
        assert info.ladder is True


def test_air_has_no_collision_shapes():
    info = BLOCK_REGISTRY.get(0)
    assert info.shapes == ()


def test_stone_is_a_full_unit_cube():
    info = BLOCK_REGISTRY.get(1)
    assert info.shapes == ((0.0, 0.0, 0.0, 1.0, 1.0, 1.0),)


def test_slab_is_a_half_height_box():
    slab_states = BLOCK_REGISTRY.states_named("minecraft:oak_slab")
    assert slab_states
    top_half = next(info for info in slab_states if info.shapes == ((0.0, 0.5, 0.0, 1.0, 1.0, 1.0),))
    assert top_half.solid is False  # not a full cube, even though it does have collision
    assert top_half.has_collision is True


def test_stairs_have_a_multi_box_l_shape():
    stair_states = BLOCK_REGISTRY.states_named("minecraft:oak_stairs")
    assert stair_states
    # Every orientation should have at least 2 boxes (the step + the riser),
    # never a single full-cube box or no shape at all.
    for info in stair_states:
        assert len(info.shapes) >= 2
        assert info.solid is False
