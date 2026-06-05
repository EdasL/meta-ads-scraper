"""Pydantic response/request models (master-plan §6 P7-T2). Mirror the frozen §3.6
matview columns; the review input enforces the §3.7 verdict enum."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel


class ShortlistRow(BaseModel):
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_id: int | None = None
    app_name: str | None = None
    gem_score: float
    scaling_velocity: float | None = None
    under_radar: float | None = None
    funnel_intent: float | None = None
    active_ad_count: int | None = None
    max_longevity_days: int | None = None
    alert: bool = False
    refreshed_at: datetime | None = None


class AppInfo(BaseModel):
    store: str
    store_id: str
    name: str | None = None
    developer: str | None = None
    genre: str | None = None
    release_date: date | None = None
    rating: float | None = None
    rating_count: int | None = None
    is_emerging: bool | None = None


class FunnelInfo(BaseModel):
    landing_url: str | None = None
    final_url_after_redirect: str | None = None
    screenshot_s3_key: str | None = None


class CreativeInfo(BaseModel):
    type: str | None = None
    s3_key: str | None = None
    body_text: str | None = None
    title: str | None = None


class GemDossier(BaseModel):
    gem: ShortlistRow
    domain: str | None = None
    page_category: str | None = None
    ig_username: str | None = None
    ig_followers: int | None = None
    parent_company: str | None = None        # resolved name of the owning advertiser, if any
    apps: list[AppInfo] = []
    funnels: list[FunnelInfo] = []
    creatives: list[CreativeInfo] = []


class ScalingPoint(BaseModel):
    week_start: date
    active_ad_count: int | None = None
    distinct_creative_count: int | None = None
    total_eu_reach: int | None = None


class ReviewQueueRow(BaseModel):
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_name: str | None = None
    gem_score: float
    active_ad_count: int | None = None
    max_longevity_days: int | None = None


class ReviewDecisionIn(BaseModel):
    verdict: Literal["accept", "reject", "hold"]
    reviewer: str
    note: str | None = None
