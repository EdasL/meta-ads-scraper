import math
from agf.scoring.embeddings import build_embed_text, content_hash, l2_normalize


def test_build_embed_text_orders_and_dedups():
    t = build_embed_text(
        app_name="VocalSplit", tagline="Isolate vocals",
        genre="Music", description="Remove vocals from any song. " * 100,
        ad_copy=["Try it free", "Try it free", "AI stem separation"])
    assert t.startswith("VocalSplit")
    assert "Isolate vocals" in t and "Music" in t
    assert t.count("Try it free") == 1          # deduped
    assert "AI stem separation" in t
    assert len(t) < 4000                         # description truncated

def test_build_embed_text_handles_missing():
    t = build_embed_text(app_name="X", tagline=None, genre=None,
                         description=None, ad_copy=[])
    assert t.strip() == "X"

def test_content_hash_stable_and_changes():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")

def test_l2_normalize_unit():
    v = l2_normalize([3.0, 4.0])
    assert math.isclose(math.hypot(*v), 1.0, rel_tol=1e-6)
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]   # zero stays zero, no div0
