from agf.discovery.liked_keyword_mining import (
    Candidate, dedup_candidates, propose_phrases)


def test_dedup_is_case_insensitive():
    cands = [Candidate("AI Vocal Remover", "r", [1]),
             Candidate("stem splitter", "r", [2])]
    out = dedup_candidates(cands, existing={"ai vocal remover"})
    assert [c.phrase for c in out] == ["stem splitter"]


def test_propose_parses_gemini_json():
    def _post(url, body):
        # emulate a ResponseSchema JSON reply in the candidates text part
        return {"candidates": [{"content": {"parts": [{"text":
            '{"phrases":[{"phrase":"ai stem separation","rationale":"like VocalSplit",'
            '"inspired_by":[7953]}]}'}]}}]}
    out = propose_phrases([{"advertiser_id": 7953, "name": "VocalSplit",
                            "description": "remove vocals", "ad_copy": ["AI stems"]}],
                          api_key="k", _post=_post)
    assert len(out) == 1
    assert out[0].phrase == "ai stem separation"
    assert out[0].inspired_by == [7953]
