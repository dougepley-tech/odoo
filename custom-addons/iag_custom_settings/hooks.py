# -*- coding: utf-8 -*-

"""Schema migration helpers, legacy config import, and US check view sync."""


def pre_init_schema(env):
    """Before ORM drops lock_unlock_group_id, copy legacy values into a temp table."""
    cr = env.cr
    cr.execute(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'iag_custom_settings'
              AND column_name = 'lock_unlock_group_id'
        )
        """
    )
    if not cr.fetchone()[0]:
        return
    cr.execute("DROP TABLE IF EXISTS iag_lock_group_migration")
    cr.execute("CREATE TABLE iag_lock_group_migration (gid INTEGER PRIMARY KEY)")
    cr.execute(
        """
        INSERT INTO iag_lock_group_migration (gid)
        SELECT DISTINCT lock_unlock_group_id
        FROM iag_custom_settings
        WHERE lock_unlock_group_id IS NOT NULL
        """
    )


def post_init_hook(env):
    cr = env.cr
    Settings = env["iag.custom.settings"].sudo()
    rec = Settings.search([], limit=1)
    if not rec:
        return

    cr.execute(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'iag_lock_group_migration'
        )
        """
    )
    if cr.fetchone()[0]:
        cr.execute("SELECT gid FROM iag_lock_group_migration")
        gids = [row[0] for row in cr.fetchall()]
        cr.execute("DROP TABLE IF EXISTS iag_lock_group_migration")
        if gids:
            rec.write({"lock_unlock_group_ids": [(6, 0, gids)]})

    param = env["ir.config_parameter"].sudo().get_param(
        "iag_custom_settings.lock_unlock_group_id"
    )
    if param:
        gid = int(param)
        merged = list(set(rec.lock_unlock_group_ids.ids + [gid]))
        rec.write({"lock_unlock_group_ids": [(6, 0, merged)]})
        env["ir.config_parameter"].sudo().set_param(
            "iag_custom_settings.lock_unlock_group_id",
            "",
        )

    rec._iag_sync_us_check_views()
