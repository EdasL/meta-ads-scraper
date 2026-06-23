from types import SimpleNamespace
from agf.scoring.vetoes import no_app

def _fv(**kw):
    base = dict(has_app=False, web_payoff_kind=None)
    base.update(kw)
    return SimpleNamespace(**base)

def test_no_app_vetoes_appless_non_websub():
    assert no_app(_fv(has_app=False, web_payoff_kind=None)) is True

def test_no_app_skips_websub():
    assert no_app(_fv(has_app=False, web_payoff_kind="web_sub")) is False

def test_no_app_passes_real_app():
    assert no_app(_fv(has_app=True, web_payoff_kind=None)) is False
