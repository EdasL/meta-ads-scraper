from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class AppMeta:
    track_id: str
    name: str
    seller: str
    genre: str
    release_date: str
    rating_count: int
    avg_rating: float
    description: str = ""
    icon_url: str | None = None        # iOS artworkUrl512 / Play icon
    screenshots: list | None = None    # list of URL strings (None when the store has none)
    installs: int | None = None        # Play maxInstalls; Apple publishes no install counts
    price_label: str | None = None     # iOS formattedPrice / Play priceText
    is_game: bool | None = None        # iTunes primaryGenreName/genreIds, Play GAME_* genreId; None = unknown


def parse_lookup(payload: dict) -> AppMeta | None:
    """Parse an iTunes Lookup response into AppMeta (None if no result).

    screenshotUrls CAN be empty while ipadScreenshotUrls is populated — fall back."""
    results = payload.get("results") or []
    if not results:
        return None
    r = results[0]
    return AppMeta(
        track_id=str(r.get("trackId", "")),
        name=r.get("trackName", ""),
        seller=r.get("sellerName", ""),
        genre=r.get("primaryGenreName", ""),
        release_date=r.get("releaseDate", ""),
        rating_count=int(r.get("userRatingCount") or 0),
        avg_rating=float(r.get("averageUserRating") or 0.0),
        description=r.get("description", ""),
        icon_url=r.get("artworkUrl512"),
        screenshots=(r.get("screenshotUrls") or r.get("ipadScreenshotUrls") or None),
        price_label=r.get("formattedPrice"),
    )


def emerging_from_release(release_date: str, now: datetime, months: int = 18) -> bool:
    """True if the app was first released within the last `months`."""
    released = datetime.fromisoformat(release_date.replace("Z", "+00:00"))
    if released.tzinfo is None:
        released = released.replace(tzinfo=timezone.utc)
    age_days = (now - released).days
    return age_days <= months * 30
