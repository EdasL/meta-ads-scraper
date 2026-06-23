from agf.scoring.run_scoring import websub_axis_override


def test_override_nulls_app_axes_for_websub():
    ur, d = websub_axis_override("web_sub", ur=0.9, d=0.8)
    assert ur is None and d is None


def test_override_passthrough_for_normal():
    ur, d = websub_axis_override(None, ur=0.9, d=0.8)
    assert ur == 0.9 and d == 0.8
