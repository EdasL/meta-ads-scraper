from agf.db.migrate import apply_migrations


def test_migrate_is_idempotent(_migrated_engine):
    # the fixture already applied every migration; a second run applies nothing
    assert apply_migrations(_migrated_engine) == []
