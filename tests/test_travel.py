from custom_components.volets_holm.travel import DOWN, UP, Travel


def test_course_complete_et_partielle():
    t = Travel(20, 10, position=0)
    assert t.start(100, 0.0) == 20
    assert t.direction == UP
    assert round(t.position(5.0)) == 25
    assert t.position(30.0) == 100
    t.stop(30.0)
    assert t.start(50, 100.0) == 5
    assert t.direction == DOWN
    assert round(t.position(102.5)) == 75
    t.stop(102.5)
    assert round(t.position(200)) == 75


def test_set_position_bornes():
    t = Travel(20, 20)
    t.set_position(150)
    assert t.position(0) == 100
    t.set_position(-3)
    assert t.position(0) == 0
