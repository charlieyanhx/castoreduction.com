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
