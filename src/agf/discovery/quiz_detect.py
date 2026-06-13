"""Quiz-screen detection on RENDERED funnel HTML (stdlib only, no DB, no network).
A question screen is recognized structurally — a sibling cluster of short clickable
answer options (buttons / role=button / labels / cursor-pointer divs) — so it works
across languages. Page copy (FUNNEL_WORDS) and progress markers only raise confidence.
Live-derived (2026-06-12, femrun/Noom/Ruut renders): options may be <button>s, or
divs with a cursor-pointer class whose visible text sits in an img alt."""
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from agf.discovery.funnel_classify import FUNNEL_WORDS

MAX_ANSWER_CHARS = 40
# Texts that make a clickable a banner/nav control, never a quiz answer.
_NOT_ANSWER = ("cookie", "consent", "accept", "allow", "agree", "manage", "privacy",
               "settings", "reject", "login", "log in", "sign in", "sign up",
               "register", "subscribe", "learn more", "contact", "menu", "search")
# Tags that never close in HTML — they must not grow the open-element stack.
_VOID = {"input", "img", "br", "hr", "meta", "link", "area", "base", "col",
         "embed", "source", "track", "wbr", "param"}
_SKIP = {"script", "style", "noscript", "template"}
# id/class-token prefixes of consent-vendor widgets (OneTrust "ot-…", Cookiebot,
# Funding Choices…). Their dialogs are CSS-hidden, which a static parser cannot see —
# live-derived: BetterMe's OneTrust filter modal scored as an Apply/Cancel "cluster".
_CMP_PREFIXES = ("ot-", "onetrust", "cookie", "consent", "cybot", "usercentrics",
                 "qc-cmp", "didomi", "gdpr", "truste", "sp_message", "fc-")


def _is_hidden(attrs: dict) -> bool:
    """True for subtrees a user cannot see: hidden/aria-hidden, inline display:none,
    or a consent-vendor container."""
    if "hidden" in attrs and attrs.get("hidden") != "false":
        return True
    if attrs.get("aria-hidden") == "true":
        return True
    style = (attrs.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    tokens = (attrs.get("class") or "").lower().split() + [(attrs.get("id") or "").lower()]
    return any(tok.startswith(p) for tok in tokens for p in _CMP_PREFIXES)


@dataclass(frozen=True)
class QuizScreen:
    """Structural signals of one rendered screen."""
    answer_cluster: tuple   # texts of the largest sibling group of answer-like clickables
    progress: bool          # <progress> / role=progressbar / progress-bar class
    quiz_words: tuple       # FUNNEL_WORDS present in the visible text
    question: bool          # an h1-h3 ending in '?'


class _Dom(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parents = []          # parent ordinal per open element (for sibling grouping)
        self._node_seq = 0
        self._skip = 0
        self._hidden_at = []        # depths where a hidden subtree started
        self._open_clicks = []      # (click_idx, depth at open)
        self._open_heads = []       # (head_idx, depth at open)
        self.clicks = []            # {"parent": int, "text": [..]}
        self.heads = []             # [text parts] per h1-h3
        self.visible = []
        self.progress = False

    @staticmethod
    def _clickable(tag, attrs):
        if tag == "button" or attrs.get("role") == "button" or tag == "label":
            return True
        return "cursor-pointer" in (attrs.get("class") or "")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        visible = not (self._skip or self._hidden_at)
        if visible and (tag == "progress" or a.get("role") == "progressbar"
                        or "progress" in (a.get("class") or "").lower()):
            self.progress = True
        if tag == "img" and a.get("alt"):
            # generic alts ("Icon", "Image") are decoration, not option text — and the
            # probe clicks by text, which never includes alt text anyway
            if a["alt"].strip().lower() not in ("icon", "image", "img", "logo",
                                                "picture", "photo"):
                self._text(a["alt"])
        if tag in _VOID:
            return
        if tag in _SKIP:
            self._skip += 1
        if _is_hidden(a):
            self._hidden_at.append(self._depth)
            visible = False
        self._node_seq += 1
        if visible and self._clickable(tag, a):
            # keep parent AND grandparent: options often sit in one-per-option
            # wrappers (BetterMe: button-in-li under a ul), where only the
            # grandparent groups the siblings
            self.clicks.append({"parent": self._parents[-1] if self._parents else 0,
                                "grandparent": self._parents[-2] if len(self._parents) > 1 else 0,
                                "text": []})
            self._open_clicks.append((len(self.clicks) - 1, self._depth))
        if tag in ("h1", "h2", "h3"):
            self.heads.append([])
            self._open_heads.append((len(self.heads) - 1, self._depth))
        self._parents.append(self._node_seq)
        self._depth += 1

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if self._depth:
            self._depth -= 1
            self._parents.pop()
        while self._hidden_at and self._hidden_at[-1] >= self._depth:
            self._hidden_at.pop()
        while self._open_clicks and self._open_clicks[-1][1] >= self._depth:
            self._open_clicks.pop()
        while self._open_heads and self._open_heads[-1][1] >= self._depth:
            self._open_heads.pop()

    def _text(self, data):
        if self._skip or self._hidden_at or not data.strip():
            return
        self.visible.append(data)
        for i, _ in self._open_clicks:
            self.clicks[i]["text"].append(data)
        for i, _ in self._open_heads:
            self.heads[i].append(data)

    def handle_data(self, data):
        self._text(data)


def _collapse(parts) -> str:
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _answer_text(parts) -> str | None:
    """The clickable's text when it looks like a quiz answer, else None. Adjacent
    duplicate parts collapse to one — an option often carries the same word as both
    img alt and text node ("Male Male" -> "Male"), and the probe clicks by text."""
    deduped = []
    for p in parts:
        p = p.strip()
        if p and (not deduped or p.lower() != deduped[-1].lower()):
            deduped.append(p)
    t = _collapse(deduped)
    if not t or len(t) > MAX_ANSWER_CHARS:
        return None
    low = t.lower()
    if any(w in low for w in _NOT_ANSWER):
        return None
    return t


def score_quiz_screen(html: str) -> QuizScreen:
    """Structural signals of one rendered screen's HTML."""
    dom = _Dom()
    try:
        dom.feed(html or "")
    except Exception:                       # malformed markup must never raise
        pass
    groups: dict = {}
    for c in dom.clicks:
        t = _answer_text(c["text"])
        if t is not None:
            groups.setdefault(("p", c["parent"]), {})[t.lower()] = t
            groups.setdefault(("g", c["grandparent"]), {})[t.lower()] = t
    cluster = max(groups.values(), key=len, default={})
    text = _collapse(dom.visible).lower()
    return QuizScreen(
        answer_cluster=tuple(cluster.values()),
        progress=dom.progress,
        quiz_words=tuple(w for w in FUNNEL_WORDS if w in text),
        question=any(_collapse(h).endswith("?") for h in dom.heads),
    )


def is_question_screen(screen: QuizScreen) -> bool:
    """An answer cluster is required; a 2-option cluster (gender screens) also needs
    corroboration so a Login/Signup button pair can't pass."""
    n = len(screen.answer_cluster)
    if n >= 3:
        return True
    return n == 2 and bool(screen.progress or screen.quiz_words or screen.question)
