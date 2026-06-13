# scripts/validate_quiz_probe.py
"""Live validation of the quiz click-probe: render+probe ONE url and print the verdict.
Run one url per process so a hung render can be killed from outside:
    PYTHONPATH=src .venv/bin/python scripts/validate_quiz_probe.py <url>
Needs the sandbox OFF (real network) and Playwright chromium installed."""
import sys

from agf.discovery.firehose_fingerprint import detect_signatures
from agf.discovery.funnel_classify import matched_funnel_host, matched_funnel_path
from agf.discovery.funnel_confirm import classify_funnel
from agf.enrich_full.funnel_capture import render_and_probe


def main() -> None:
    url = sys.argv[1]
    host = matched_funnel_host(url)
    probed = render_and_probe(url)
    sigs = detect_signatures(probed.landing_html)
    kind, status, _, _, reason = classify_funnel(probed.landing_html, None, host,
                                                 probed.quiz_steps)
    verdict = f"{kind}/{status}" + (f"({reason})" if reason else "")
    print(f"{url}\n  host_hint={host} path_hint={matched_funnel_path(url)} "
          f"sigs={sorted(sigs)} quiz_steps={probed.quiz_steps} verdict={verdict}")


if __name__ == "__main__":
    main()
