import agf.workers.main as m


def test_websub_cap_default(monkeypatch):
    monkeypatch.delenv("AGF_WEBSUB_BRIDGE_PER_CYCLE", raising=False)
    assert m.websub_bridge_per_cycle() == 10


def test_websub_cap_env(monkeypatch):
    monkeypatch.setenv("AGF_WEBSUB_BRIDGE_PER_CYCLE", "3")
    assert m.websub_bridge_per_cycle() == 3
