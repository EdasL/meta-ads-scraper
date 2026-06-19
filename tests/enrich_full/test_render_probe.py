# tests/enrich_full/test_render_probe.py — the quiz click-probe (render_and_probe).
from agf.enrich_full.funnel_capture import render_and_probe

Q1 = ("<html><body><p>1-min quiz</p><div><button>Male</button>"
      "<button>Female</button></div></body></html>")
Q2 = ("<html><body><p>1-min quiz</p><div><button>Under 30</button>"
      "<button>30-45</button><button>Over 45</button></div></body></html>")
DEAD_END = "<html><body><h1>Sorry, women only</h1><button>Back to site</button></body></html>"
MARKETING = "<html><body><h1>Best app</h1><a href='/p'>Pricing</a></body></html>"
SPLASH = ("<html><body><h1>Quiz: Which workout fits you?</h1>"
          "<button>CONTINUE</button></body></html>")
Q1_MMP = Q1.replace("</body>", '<script src="https://cdn.branch.io/x.js"></script></body>')


class FakeQuizPage:
    """Scripted SPA with history: clicking an answer follows the `routes` map keyed by
    (current screen index, clicked text); go_back pops to the previous screen."""
    def __init__(self, screens, routes):
        self.screens = screens
        self.routes = routes
        self.i = 0
        self.history = []
        self.clicked = []

    def goto(self, url): pass
    def go_back(self): self.i = self.history.pop()
    def wait_for_load_state(self, state, timeout=None): pass
    def wait_for_timeout(self, ms): pass

    def query_selector(self, sel):
        if not sel.startswith('text='):
            return None                      # CMP selectors etc.
        t = sel[len('text='):].strip('"')
        # unquoted text= is a case-insensitive substring match in Playwright
        if t.lower() not in self.screens[self.i].lower():
            return None
        page = self
        class _El:
            def click(self):
                page.clicked.append(t)
                nxt = page.routes.get((page.i, t))
                if nxt is not None:
                    page.history.append(page.i)
                    page.i = nxt
        return _El()

    def content(self):
        return self.screens[self.i]


def test_probe_confirms_quiz_and_stops_at_two_steps():
    page = FakeQuizPage([Q1, Q2], routes={(0, "Male"): 1})
    got = render_and_probe("https://quiz.x.com", page=page)
    assert got.quiz_steps == 2
    assert got.landing_html == Q1
    assert got.screens == (Q1, Q2)
    assert page.clicked == ["Male"]          # confirmed at 2 steps - no further clicks


def test_probe_rolls_back_a_dead_end_and_tries_the_next_answer():
    # the femrun case: the first answer screens the visitor out; the second continues
    page = FakeQuizPage([Q1, DEAD_END, Q2],
                        routes={(0, "Male"): 1, (0, "Female"): 2})
    got = render_and_probe("https://quiz.x.com", page=page)
    assert got.quiz_steps == 2
    assert page.clicked == ["Male", "Female"]
    assert got.screens == (Q1, Q2)


def test_probe_hops_a_splash_start_cta():
    # the BetterMe case: the landing is a "Quiz: ..." splash with a lone CONTINUE;
    # the first real question is one CTA hop away
    page = FakeQuizPage([SPLASH, Q1, Q2],
                        routes={(0, "continue"): 1, (1, "Male"): 2})
    got = render_and_probe("https://quiz.x.com", page=page)
    assert got.quiz_steps == 2
    assert page.clicked == ["continue", "Male"]
    assert got.screens == (SPLASH, Q1, Q2)


def test_probe_no_cta_hop_without_quiz_copy():
    page = FakeQuizPage([MARKETING], routes={})
    got = render_and_probe("https://x.com", page=page)
    assert got.quiz_steps == 0
    assert page.clicked == []                # no quiz words -> the CTA hop never fires


def test_probe_single_screen_quiz_stops_at_one_step():
    page = FakeQuizPage([Q1, MARKETING], routes={(0, "Male"): 1, (0, "Female"): 1})
    got = render_and_probe("https://quiz.x.com", page=page)
    assert got.quiz_steps == 1               # every answer leads off the quiz


def test_probe_skips_clicks_when_payoff_already_present():
    page = FakeQuizPage([Q1_MMP, Q2], routes={(0, "Male"): 1})
    got = render_and_probe("https://quiz.x.com", page=page)
    assert got.quiz_steps == 1
    assert page.clicked == []                # MMP on the landing -> no clicks spent


def test_probe_size_picker_lookalike_never_confirms():
    # an e-commerce size picker IS a sibling cluster of short options and may score as
    # one question screen — but clicking an option changes nothing, so it can never
    # reach the 2 steps that classify_funnel requires
    size_picker = ("<html><body><p>Find your personalized fit plan</p>"
                   "<div><button>S</button><button>M</button><button>L</button>"
                   "<button>XL</button></div></body></html>")
    page = FakeQuizPage([size_picker], routes={})
    got = render_and_probe("https://shop.example.com/product", page=page)
    assert got.quiz_steps == 1
    from agf.discovery.funnel_confirm import classify_funnel
    from types import SimpleNamespace
    _, status, _, _, reason, _ = classify_funnel(got.landing_html,
                                              SimpleNamespace(kind="web", store_id=None),
                                              None, got.quiz_steps)
    assert (status, reason) == ("dropped", "no_quiz_no_payoff")


def test_probe_non_quiz_landing_returns_zero_steps():
    page = FakeQuizPage([MARKETING], routes={})
    got = render_and_probe("https://x.com", page=page)
    assert got.quiz_steps == 0
    assert got.landing_html == MARKETING
    assert page.clicked == []
