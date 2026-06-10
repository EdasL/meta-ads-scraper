"""SQLAlchemy Core Table objects mirroring db/schema/*.sql (master-plan §3.5).
Used by the drift test (test_models_match_schema) and available to callers; the
repo uses parameterized SQL directly for clear upsert semantics."""
from sqlalchemy import (
    MetaData, Table, Column, Integer, BigInteger, SmallInteger, Text, Boolean,
    Float, Date, TIMESTAMP, ForeignKey, UniqueConstraint, ARRAY,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

domains = Table(
    "domains", metadata,
    Column("id", Integer, primary_key=True),
    Column("domain", Text, unique=True, nullable=False),
    Column("first_seen", TIMESTAMP(timezone=True)),
)

advertisers = Table(
    "advertisers", metadata,
    Column("id", Integer, primary_key=True),
    Column("page_id", Text, unique=True, nullable=False),
    Column("page_name", Text),
    Column("domain_id", Integer, ForeignKey("domains.id")),
    Column("parent_company_id", Integer, ForeignKey("advertisers.id")),
    Column("page_category", Text),
    Column("ig_username", Text),
    Column("ig_followers", Integer),
    Column("page_like_count", BigInteger),
    Column("meta_active_ads", Integer),
    Column("meta_active_ads_capped", Boolean),
    Column("meta_active_ads_checked_at", TIMESTAMP(timezone=True)),
    Column("first_seen", TIMESTAMP(timezone=True)),
    Column("last_seen", TIMESTAMP(timezone=True)),
)

apps = Table(
    "apps", metadata,
    Column("id", Integer, primary_key=True),
    Column("store", Text, nullable=False),
    Column("store_id", Text, nullable=False),
    Column("name", Text),
    Column("developer", Text),
    Column("genre", Text),
    Column("release_date", Date),
    Column("rating", Float),
    Column("rating_count", Integer),
    Column("installs", BigInteger),
    Column("free", Boolean),
    Column("icon_url", Text),
    Column("screenshots", JSONB),
    Column("price_label", Text),
    Column("is_emerging", Boolean),
    Column("description", Text),
    Column("first_seen", TIMESTAMP(timezone=True)),
    Column("updated_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("store", "store_id"),
)

app_reviews = Table(
    "app_reviews", metadata,
    Column("app_id", Integer, ForeignKey("apps.id"), primary_key=True),
    Column("review_id", Text, primary_key=True),
    Column("author", Text),
    Column("rating", SmallInteger),
    Column("title", Text),
    Column("content", Text),
    Column("reviewed_at", TIMESTAMP(timezone=True)),
    Column("fetched_at", TIMESTAMP(timezone=True)),
)

ads = Table(
    "ads", metadata,
    Column("id", Integer, primary_key=True),
    Column("external_ad_id", Text, unique=True, nullable=False),
    Column("advertiser_id", Integer, ForeignKey("advertisers.id")),
    Column("app_id", Integer, ForeignKey("apps.id")),
    Column("link_url", Text),
    Column("cta_type", Text),
    Column("cta_text", Text),
    Column("publisher_platforms", ARRAY(Text)),
    Column("is_active", Boolean),
    Column("start_date", TIMESTAMP(timezone=True)),
    Column("end_date", TIMESTAMP(timezone=True)),
    Column("collation_count", Integer),
    Column("first_seen", TIMESTAMP(timezone=True)),
    Column("last_seen", TIMESTAMP(timezone=True)),
)

creatives = Table(
    "creatives", metadata,
    Column("id", Integer, primary_key=True),
    Column("ad_id", Integer, ForeignKey("ads.id"), nullable=False),
    Column("type", Text),
    Column("s3_key", Text),
    Column("content_type", Text),
    Column("file_size", BigInteger),
    Column("sha256", Text),
    Column("phash", Text),
    Column("body_text", Text),
    Column("title", Text),
    Column("snapshot_image_url", Text),
    Column("first_seen", TIMESTAMP(timezone=True)),
)

funnels = Table(
    "funnels", metadata,
    Column("id", Integer, primary_key=True),
    Column("ad_id", Integer, ForeignKey("ads.id"), unique=True, nullable=False),
    Column("landing_url", Text),
    Column("final_url_after_redirect", Text),
    Column("screenshot_s3_key", Text),
    Column("captured_at", TIMESTAMP(timezone=True)),
)

app_metrics = Table(
    "app_metrics", metadata,
    Column("app_id", Integer, primary_key=True),
    Column("snapshot_date", Date, primary_key=True),
    Column("rating", Float),
    Column("rating_count", BigInteger),
    Column("installs", BigInteger),
    schema="adtech",
)

ad_metrics = Table(
    "ad_metrics", metadata,
    Column("ad_id", Integer, primary_key=True),
    Column("snapshot_date", Date, primary_key=True),
    Column("eu_total_reach", BigInteger),
    Column("active", Boolean),
    schema="adtech",
)

app_advertiser_metrics = Table(
    "app_advertiser_metrics", metadata,
    Column("advertiser_id", Integer, primary_key=True),
    Column("week_start", Date, primary_key=True),
    Column("active_ad_count", Integer),
    Column("distinct_creative_count", Integer),
    Column("total_eu_reach", BigInteger),
    schema="adtech",
)

advertiser_growth = Table(
    "advertiser_growth", metadata,
    Column("advertiser_id", Integer, ForeignKey("advertisers.id"), primary_key=True),
    Column("first_count", Integer),
    Column("latest_count", Integer),
    Column("weeks_observed", Integer),
    Column("pct_change", Float),
    Column("computed_at", TIMESTAMP(timezone=True)),
    schema="adtech",
)

advertiser_explainers = Table(
    "advertiser_explainers", metadata,
    Column("advertiser_id", Integer, ForeignKey("advertisers.id"), primary_key=True),
    Column("what_it_does", Text, nullable=False),
    Column("problem_solved", Text, nullable=False),
    Column("why_working", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("source_fingerprint", Text, nullable=False),
    Column("generated_at", TIMESTAMP(timezone=True)),
    schema="adtech",
)

crawl_log = Table(
    "crawl_log", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Integer, nullable=False),
    Column("crawled_at", TIMESTAMP(timezone=True)),
    Column("crawl_result", Text),
    schema="logging",
)
