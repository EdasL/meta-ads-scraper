import math
import pytest
from agf.scoring.embeddings import embed_texts, EmbedError


def _fake_post_factory(values):
    def _post(url, body):
        assert body["outputDimensionality"] == 768
        return {"embedding": {"values": values}}
    return _post


def test_embed_texts_normalizes():
    out = embed_texts(["hi"], api_key="k", _post=_fake_post_factory([3.0, 4.0] + [0.0] * 766))
    assert len(out) == 1
    assert math.isclose(math.sqrt(sum(x * x for x in out[0])), 1.0, rel_tol=1e-6)


def test_embed_texts_dim_mismatch_raises():
    with pytest.raises(EmbedError):
        embed_texts(["hi"], api_key="k", _post=_fake_post_factory([1.0, 2.0]))  # only 2 dims
