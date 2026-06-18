from sqlalchemy import text


def test_preference_scores_table_exists(_migrated_engine):
    with _migrated_engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='frontend' AND table_name='preference_scores'"
            )).fetchall()
        }
    assert {
        "user_email", "advertiser_id", "p_like", "uncertainty",
        "queue_rank", "why", "model_version", "scored_at",
    } <= cols
