import pytest
from pydantic import ValidationError
from agf.api.schemas import ShortlistRow, GemDossier, ReviewDecisionIn, ReviewQueueRow


def test_shortlist_row_validates_and_requires_score():
    row = ShortlistRow(advertiser_id=1, cohort="C", page_name="P", app_id=None,
                       app_name=None, gem_score=80.0, scaling_velocity=0.9,
                       under_radar=0.8, funnel_intent=0.7, active_ad_count=5,
                       max_longevity_days=30, alert=True)
    assert row.gem_score == 80.0 and row.app_id is None
    with pytest.raises(ValidationError):
        ShortlistRow(advertiser_id=1, cohort="C", page_name="P")   # missing gem_score


def test_review_decision_in_rejects_bad_verdict():
    assert ReviewDecisionIn(verdict="accept", reviewer="me").verdict == "accept"
    with pytest.raises(ValidationError):
        ReviewDecisionIn(verdict="maybe", reviewer="me")


def test_queue_row_and_dossier_construct():
    ReviewQueueRow(advertiser_id=1, cohort="C", page_name="P", app_name=None, gem_score=75.0)
    GemDossier(gem=ShortlistRow(advertiser_id=1, cohort="C", page_name="P", app_id=None,
        app_name=None, gem_score=80.0, scaling_velocity=0.9, under_radar=0.8,
        funnel_intent=0.7, active_ad_count=5, max_longevity_days=30, alert=True),
        domain="x.com", page_category="Software", apps=[], funnels=[], creatives=[])
