# list of tests

Allow a monitor to define multiple versions of the same test.
Allow the monitor to specify an additional key after the test type to define mulitple instances of the same test type.

- if a test defines a duplicate key, only the last test with that key will be run.  This should generate error logging to indicate that the duplicate test was skipped. This should allow template overrides to be used instead of the base template definition.
- errors should be handled the same way as duplicate monitor names
- enforce and skip duplicate keys per monitor.  If a global duplicate key is detected log a warning during validate but do not skip the test.
- in the event of a duplicate key, the last test should run in it's own slot / position.

- the instance name suffix should be stripped in order to determine the test type for the tech.
- it should be required that all future tests have the format `[test.<test_type>.<instance_name>]` to avoid ambiguity.

- if no key is defined than the instance name should be used as the key.
- if the instance name is used as the key, then the key format should be `<monitor_name>.<test_type>.<instance_name>` to avoid ambiguity.
- `<instance_name>` should not be required.  A test without an instance name should be treated as having an instance name of `default`.

- `test.custom` should have a special case where the instance name is the 4th key instead of the 3rd


```toml
[test.html_200.instance1]
key = "x.y.z"
url = "https://example.com"

[test.html_200.instance2]
key = "x.y.a"
url = "https://2.example.com"


[test.html_200.instance3]
key = "x.y.another"
url = "https://another.example.com"

[test.custom.magic_py.instance1]
key = "network.appgrup.app.html"
command = "test_run"
args = ["--url", "https://example.com", "--jq", ".application.name"]

[test.custom.magic_py.instance2]
key = "network.appgrup.app.xml"
command = "test_run"
args = ["--url", "https://example.com", "--jq", ".application.name"]
```


## Implementation summary

Implemented in session `session_01BP7epMGpjee7w5DmMLcVux`.

`[test.<test_type>.<instance>]` sections now load as separate tests of the same
type.  The loader already recursed into nested sections (that is how
`[test.custom.<module>]` works), so the work was splitting the flattened
section path into a type and an instance, and keeping the instance out of the
type.

- `locallib/monitor_models.py` - `DEFAULT_TEST_INSTANCE = "default"`;
  `TestConfig` gains `instance` (last field, so positional construction is
  unchanged) and a `full_name` property used in log and error messages.
  `test_type` stays the bare registered type, so the stored
  `monitor_test_results.test_type` and the report field selectors that match on
  it (`report_data_builder._matches_test`) are unaffected.
- `locallib/monitor_loader.py` - `_split_test_name` takes the first segment as
  the type, or the first two when it is `custom`, and the rest as the instance,
  defaulting to `default`.  A missing `key` becomes
  `<monitor>.<test_type>.<instance>`.  `_drop_duplicate_keys` keeps the last
  test for a key in its own position and reports each skipped one;
  `_check_global_test_keys` reports a key shared between monitors without
  skipping anything.  Both go through `_record_error`, which appends to
  `load_errors` and logs a warning, the same handling as a duplicate monitor
  name.  `_flatten_tests` and `_build_tests` became instance methods to reach
  `load_errors`, and now report a `[[test.x]]` array of tables instead of
  silently folding it into the options of a phantom test.
- `locallib/monitor_test_factory.py`, `locallib/monitor_runner.py` - messages
  use `full_name` so instances are distinguishable; no functional change.
- `docs/monitors.md`, `monitors/sample.toml`, `docs/architecture.md` -
  documented instances, key defaulting, and the duplicate-key rules.
- `tests/unit/test_monitor_loader.py` - nine cases covering instance loading,
  the custom four-segment case, key defaulting, the default instance,
  last-wins duplicates, a template test replaced by key, the global duplicate
  warning, and the rejected array-of-tables form.

Two notes on behaviour worth knowing:

- No `[test.*]` section is invalid for lack of a key.  Every section has an
  instance - `default` when none is written - so a keyless `[test.ping]`
  derives `<monitor>.ping.default`.  Every test in `monitors/` has an explicit
  key, so nothing changed for existing monitors.
- A test type written without an instance can still only appear once in a
  monitor, since toml rejects a redefined table.  `docs/monitors.md` therefore
  recommends naming an instance on new sections rather than making the bare
  form a load error, which would break every monitor in the repo.
- "The last test runs in its own slot" is relative to the loader's flattened
  order, not raw file order.  toml groups every instance of one type under a
  single table, so all `[test.html_200.*]` sections are ordered together
  regardless of where they appear in the file.

### Review follow up

A code review of the first commit found that the section walk recursed without
a limit, so a table valued option - `headers` on the `html_*` tests, or a
custom test's `args` written as a list of tables - was read as another
instance: the option was stripped from its test and a phantom test appeared
under it.  The walk now stops at the instance name (one level deeper for a
custom test's module), so anything nested below that is options.  This also
fixed the same bug in the pre-existing behaviour, where `headers` was dropped
before instances existed.  The derived key is logged at warning level again,
as it was before this feature.

Two review points were accepted as they stand rather than changed:

- The derived key gaining a `.default` segment is a breaking change for any
  deployment with unkeyed tests - the result history restarts under the new
  key, and a report field named `<monitor>.<test_type>` stops matching.  This
  is spec'd behaviour and is flagged as `BREAKING CHANGE` on the commit.
- A duplicate key inside one monitor stops the earlier test from running, and
  the only signal is a load time warning that `monmon validate` reports - a
  skipped test raises no alert of its own.

`uv run pytest` - 500 passed.  `monmon validate` loads the 10 existing
monitors with 0 warnings.
