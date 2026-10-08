"""app.models — web-layer model modules for the matapp framtidsvision.

Phase 2 (T1) db-foundation: every model here declares its table on the shared
``database.Base`` and is imported BEFORE ``init_db`` (POC fix-2 idiom). Importing
``app.models`` (or any submodule) registers the table so ``boot()`` creates it
atomically with the rest of the schema.

DoD-required names: ``users``, ``profile``, ``offers_db`` — each carries a ``Base``
that is the shared ``database.Base`` (``print(users.Base, profile.Base)``).
"""
from . import users      # noqa: F401  (registers users table on Base)
from . import profile    # noqa: F401  (registers profile table on Base)
from . import offers_db  # noqa: F401  (registers offers table on Base)
# MC 10348: the global store_selection model is RETIRED — the store choice
# lives on the per-user profile row (profile.selected_stores); the old table
# may linger in existing DBs but nothing reads or writes it (docs/ARCHITECTURE.md).

# MC 1355.18 (T11 DA c2 P0): the recipes ORM model must be registered on the
# shared Base BEFORE boot()/create_all — nothing else in the app path imports
# src.recipes.store, so without this the ``recipes`` table is never created and
# ensure_columns' inspector raises NoSuchTableError on the kid_friendly ALTER.
# MC 10037 (P1-a0): the direct import is retired behind the recipes_db shim
# (offers_db precedent) — app code imports recipes ONLY via app.models.recipes_db.
from . import recipes_db  # noqa: F401  (registers recipes table on Base via the shim)
from . import recipe_usage  # noqa: F401  (registers recipe_usage table, MC 10037 P1-a)
from . import recipe_rating  # noqa: F401  (registers recipe_rating table, MC 10037 P1-a)
