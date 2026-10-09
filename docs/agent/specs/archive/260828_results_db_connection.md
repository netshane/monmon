# results db connection

Add a connection automatically to the [connections] section of the monitor config.
This connection should be named `results_db` and should reference the connection string defined for results_db

- if a value for results_db is defined in [connections], the explicit value should be used instead of the default connection string.
- keep the `get_results_engine()` separate from teh [connections] configuration.  It should always use the `results_db` connection string defined in the [default] section of the monitor config.
- it should appear in any "list connections" output 
- do not worry about db isolation at this time.  The risk is acceptable

- add unit tests and update documentation.

- build a connections command to list defined connections.  It should show full connection strings and redact secrets.
- this should list all defined connections
- this should offer a `--json` option to output the connections in json format
- do not visually flag results_db in this output
- for `{passwd}` in the connection string, don't redact the token since it is not a secret.

```toml
[default]
results_db = "sqlite:///results.db"

[default.connections]
# results_db is defined auomatically
db_prd6 = "mssql+mssql-python://Server=someserver;Database=somedb;TrustServerCertificate=yes;Encrypt=yes;Trusted_Connection=yes"

[test.db_flag]
key = "network.appgroup.app.dbflag"
connection = "results_db" # this should connect to the results_db database
query =  """ 
SELECT 
    COUNT(*) 
FROM Monitors AS a
WHERE a.Monitor = 'SomeMonitor' 
  AND a.Status = 'Failed'
  AND a.ScanDate > datetime('now', '-1 day')
"""
```

---

## Implementation summary

Claude session: `session_01AvnoUKdR7SVePzMm8DRqo4`

- **`locallib/dependencies.py`** - added `_connections_with_results_db(settings)`,
  which copies `[connections]` and `setdefault`s `results_db` to `settings.results_db`
  (an explicit `[connections].results_db` wins). `get_db_factory()` now builds the
  `DbConnectionFactory` from it, so any monitor test can use `connection = "results_db"`.
  `get_results_engine()` is unchanged - it still reads `settings.results_db` directly.
  New `get_connection_inspector()` factory.
- **`locallib/connection_inspector.py`** (new) - `ConnectionInspector` produces a
  sorted, secret-redacted `list[ConnectionInfo]` (`name`, `kind`, `connection`).
  `kind` is `db` / `docker` / `opensearch`, classified as in
  `MonitorRunner._register_connections`. Redaction (regex based): `scheme://user:pw@host`
  userinfo up to the last `@` of the authority (covers passwords with an unescaped
  `@`), `PWD=`/`PASSWORD=` tokens in odbc/dsn and url query strings (including `{...}`
  braced values containing `;`), and secret-named keys in docker/opensearch tables.
  The `{passwd}` placeholder is deliberately left intact.
- **`monmon.py`** - new `connections` command listing all defined connections as a
  table, or JSON with `--json`. `results_db` is not visually flagged.
- **Docs** - `docs/monitors.md` `[connections]` section, `docs/architecture.md`
  supporting-modules table, `README.md` command list.
- **Tests** - `tests/unit/test_connection_inspector.py`,
  `tests/unit/test_connections_command.py` (results_db default + explicit override,
  command table/JSON output, db factory can create the `results_db` engine,
  redaction edge cases: unescaped `@`, braced `;` password, url query params).
  Full `unit` suite green (548 passed).
