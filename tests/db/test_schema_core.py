from sqlalchemy import inspect


def test_core_tables_exist(pg_conn):
    insp = inspect(pg_conn)
    for t in ("domains", "advertisers", "apps", "ads", "creatives", "funnels"):
        assert insp.has_table(t, schema="public"), t


def test_unique_keys_and_self_fk(pg_conn):
    insp = inspect(pg_conn)
    # advertisers.page_id UNIQUE
    adv_uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("advertisers", schema="public")}
    assert ("page_id",) in adv_uniques
    # apps UNIQUE(store, store_id)
    app_uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("apps", schema="public")}
    assert ("store", "store_id") in app_uniques
    # ads.external_ad_id UNIQUE
    ad_uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("ads", schema="public")}
    assert ("external_ad_id",) in ad_uniques
    # advertisers.parent_company_id self-FK -> advertisers
    fks = insp.get_foreign_keys("advertisers", schema="public")
    assert any(fk["referred_table"] == "advertisers" and fk["constrained_columns"] == ["parent_company_id"]
               for fk in fks)
