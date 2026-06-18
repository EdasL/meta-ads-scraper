from sqlalchemy import text

def test_app_ratings_table_and_check(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, comment) "
        "VALUES ('u@x.com', 1, 'like', 'nice')"))
    row = pg_conn.execute(text(
        "SELECT rating, comment, features FROM frontend.app_ratings "
        "WHERE user_email='u@x.com' AND advertiser_id=1")).one()
    assert row.rating == 'like' and row.comment == 'nice' and row.features == {}
    # CHECK rejects a bad rating
    import pytest, sqlalchemy
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        pg_conn.execute(text(
            "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) "
            "VALUES ('u@x.com', 2, 'love')"))
