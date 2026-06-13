# tests/discovery/test_quiz_detect.py
from agf.discovery.quiz_detect import is_question_screen, score_quiz_screen


def _screen(options, extra="", tag="button"):
    opts = "".join(f"<{tag}>{o}</{tag}>" for o in options)
    return f"<html><body>{extra}<div>{opts}</div></body></html>"


def test_three_plus_options_is_a_question_screen():
    s = score_quiz_screen(_screen(["Lose 1-10 kg", "Lose 11-20 kg", "Maintain weight"]))
    assert s.answer_cluster == ("Lose 1-10 kg", "Lose 11-20 kg", "Maintain weight")
    assert is_question_screen(s) is True


def test_two_options_need_corroboration():
    bare = score_quiz_screen(_screen(["Male", "Female"]))
    assert is_question_screen(bare) is False
    with_words = score_quiz_screen(_screen(["Male", "Female"], extra="<p>1-min quiz</p>"))
    assert "quiz" in with_words.quiz_words
    assert is_question_screen(with_words) is True
    with_progress = score_quiz_screen(_screen(["Male", "Female"],
                                              extra='<div role="progressbar"></div>'))
    assert with_progress.progress and is_question_screen(with_progress) is True
    with_question = score_quiz_screen(_screen(["Male", "Female"],
                                              extra="<h2>What is your gender?</h2>"))
    assert with_question.question and is_question_screen(with_question) is True


def test_nav_and_consent_buttons_never_count_as_answers():
    s = score_quiz_screen(_screen(["Log in", "Sign up", "Accept all cookies"],
                                  extra="<p>take the quiz</p>"))
    assert s.answer_cluster == ()
    assert is_question_screen(s) is False


def test_options_cluster_through_one_wrapper_level():
    # the BetterMe pattern: one wrapper (li) per option, options grouped by grandparent
    html = ("<html><body><ul><li><button>One</button></li>"
            "<li><button>Two</button></li><li><button>Three</button></li></ul></body></html>")
    s = score_quiz_screen(html)
    assert s.answer_cluster == ("One", "Two", "Three")
    assert is_question_screen(s) is True


def test_options_two_wrapper_levels_apart_do_not_cluster():
    html = ("<html><body>"
            "<section><div><button>One</button></div></section>"
            "<section><div><button>Two</button></div></section>"
            "<section><div><button>Three</button></div></section>"
            "</body></html>")
    s = score_quiz_screen(html)
    assert len(s.answer_cluster) <= 1
    assert is_question_screen(s) is False


def test_long_text_clickables_are_not_answers():
    long = "This is a very long marketing call to action that keeps going on"
    s = score_quiz_screen(_screen([long, long + " 2", long + " 3"]))
    assert s.answer_cluster == ()


def test_cursor_pointer_divs_with_img_alt_dedupe():
    # the Ruut pattern: options are divs with a cursor-pointer class; the option text
    # appears both as img alt and as a text node — it must collapse to one
    opt = ('<div class="x cursor-pointer y"><img alt="{t}"><span>{t}</span></div>')
    html = ("<html><body><p>1-min quiz</p><div>"
            + opt.format(t="Male") + opt.format(t="Female") + "</div></body></html>")
    s = score_quiz_screen(html)
    assert s.answer_cluster == ("Male", "Female")
    assert is_question_screen(s) is True


def test_role_button_and_label_count_as_clickable():
    html = ('<html><body><div><span role="button">A bit</span>'
            '<label>Quite a bit</label><span role="button">Not at all</span></div>'
            "</body></html>")
    s = score_quiz_screen(html)
    assert len(s.answer_cluster) == 3


def test_marketing_page_is_not_a_question_screen():
    html = ("<html><body><h1>The best running app</h1>"
            "<a href='/pricing'>Pricing</a><a href='/about'>About</a>"
            "<button>Get started</button></body></html>")
    assert is_question_screen(score_quiz_screen(html)) is False


def test_malformed_html_never_raises():
    s = score_quiz_screen("<div><button>Yes<div></button></span>&bogus;<b")
    assert isinstance(s.answer_cluster, tuple)
    assert score_quiz_screen("").answer_cluster == ()
    assert score_quiz_screen(None).answer_cluster == ()


def test_hidden_and_consent_subtrees_are_ignored():
    # the BetterMe case: OneTrust's CSS-hidden filter modal must not become a cluster
    onetrust = ('<section id="ot-fltr-modal"><div><button>Apply</button>'
                "<button>Cancel</button></div></section>")
    html = f"<html><body>{onetrust}<h1>Marketing page</h1></body></html>"
    assert score_quiz_screen(html).answer_cluster == ()
    aria = ('<div aria-hidden="true"><div><button>Yes</button><button>No</button>'
            "<button>Maybe</button></div></div>")
    assert score_quiz_screen(f"<html><body>{aria}</body></html>").answer_cluster == ()
    inline = ('<div style="display: none"><div><button>A</button><button>B</button>'
              "<button>C</button></div></div>")
    assert score_quiz_screen(f"<html><body>{inline}</body></html>").answer_cluster == ()


def test_script_and_style_text_is_invisible():
    html = ("<html><head><script>var quiz='personalized plan';</script></head>"
            "<body><button>Go</button></body></html>")
    s = score_quiz_screen(html)
    assert s.quiz_words == ()
