"""Round completion message — compose (pure) + insert into the hunt thread."""
from sqlalchemy import text

from agf.db.hunts import compose_round_message, insert_round_message, round_summary_counts


def test_compose_round_message_copy():
    msg = compose_round_message(7, "done", {"found": 40, "kept": 12, "off_target": 3})
    assert msg == "Round 7 done: 12 kept of 40 found, 3 off target."
    msg = compose_round_message(7, "done", {"found": 40, "kept": 12, "off_target": 0})
    assert msg == "Round 7 done: 12 kept of 40 found."
    msg = compose_round_message(8, "error", {}, error="proxy pool exhausted")
    assert msg == "Round 8 failed: proxy pool exhausted."


def test_round_summary_counts(pg_conn):
    hunt_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) VALUES ('c@t.local', 'cnt') RETURNING id"
    )).scalar_one()
    round_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms) VALUES (:h, '[]') RETURNING id"
    ), {"h": hunt_id}).scalar_one()

    def _add(page_id, triage, relevance):
        pg_conn.execute(text(
            "INSERT INTO frontend.hunt_results "
            "(round_id, page_id, page_name, store, store_id, link_url, source_term, "
            " was_new, triage, relevance) "
            "VALUES (:r, :p, 'n', 'ios', :p, 'u', 't', true, :tr, :rel)"),
            {"r": round_id, "p": page_id, "tr": triage, "rel": relevance})

    _add("1", "kept", "relevant")
    _add("2", "kept", "off_target")
    _add("3", "vetoed", "off_target")
    _add("4", "pending", None)

    counts = round_summary_counts(pg_conn, round_id)
    assert counts == {"found": 4, "kept": 2, "off_target": 2}


def test_insert_round_message_lands_in_thread(pg_conn):
    hunt_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) VALUES ('m@t.local', 'msg') RETURNING id"
    )).scalar_one()
    round_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms) VALUES (:h, '[]') RETURNING id"
    ), {"h": hunt_id}).scalar_one()
    insert_round_message(pg_conn, round_id, "Round 1 done: 2 kept of 5 found.")
    row = pg_conn.execute(text(
        "SELECT role, content FROM frontend.hunt_messages WHERE hunt_id = :h"), {"h": hunt_id}).one()
    assert row.role == "assistant"
    assert row.content == "Round 1 done: 2 kept of 5 found."
