from pathlib import Path
from agf.bootstrap import extract_lsd, extract_doc_id

HTML = (Path(__file__).parent / "fixtures" / "ads_library_page.html").read_text()


def test_extract_lsd():
    assert extract_lsd(HTML) == "AVxyz123"


def test_extract_doc_id_finds_value():
    assert extract_doc_id(HTML, fallback="000") == "27201872659451053"


def test_extract_doc_id_uses_fallback_when_missing():
    assert extract_doc_id("<html></html>", fallback="999") == "999"


def test_extract_lsd_raises_when_missing():
    import pytest
    with pytest.raises(ValueError):
        extract_lsd("<html></html>")
