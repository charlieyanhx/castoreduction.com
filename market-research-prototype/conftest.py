"""Session-wide pytest isolation: point the jobs DB at a fresh temp file so no
test run can ever read or write the production .jobs.sqlite. jobs._db_path()
resolves JOBS_DB_PATH per connection, so this works regardless of import order.
Individual files may override with their own temp path (test_api.py does)."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(prefix="castor-test-jobs-", suffix=".sqlite",
                                   delete=False)
_tmp.close()
os.environ["JOBS_DB_PATH"] = _tmp.name


# Geocode-cache isolation (P5 follow-through): geocode_address caches to disk, and a
# suite full of MOCKED geocodes was writing fake coordinates into the production cache
# — seven poisoned entries measured on 2026-08-20 — which live runs would then serve.
# Same contract as the jobs DB above: tests get a throwaway dir, always.
_geo_tmp = tempfile.mkdtemp(prefix="castor-test-geocache-")
os.environ["CASTOR_GEO_CACHE_DIR"] = _geo_tmp
