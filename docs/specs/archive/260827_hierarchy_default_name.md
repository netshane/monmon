# hierarchy default name

The `node_name` property of a hierarchy or alternate hiearchy should default to the name of the monitor if not specified.

A bare hierarchy section should not be permitted.  If it is a root node than a node_name must be specified.

If no node name is specified for an alternate hierarchy, it should default to the monitor name + '-' + the alternate hierarchy name.

If the parent_node matches the monitor name or default monitor name, it should generate a warning and this should be treated as a root parent node.

The resolved node_name should remain internal and should not add any new logging.

Add unit tests and update documentation.

```toml
[settings]
name = "my_monitor-test"

[hierarchy]
# node_name = "my_hierarchy" # optional, defaults to monitor name
parent_node = "my_parent"

[hierarchy.A]
# node_name = "my_hierarchy" # optional, defaults to monitor name + '-' + alternate hierarchy name.  In this case, "my_monitor-test-A"
parent_node = "my_parent_A"

[hierarchy.B]
node_name = "my_hierarchy-B" # since specified, this will be used as the node name in the B hierarchy
parent_node = "my_parent_B"
```

## Implementation summary

Session: session_01L9dctbqdaRZ48KoeTbULpG

`MonitorLoader._build_hierarchies` now takes the monitor name and applies the
defaults (`locallib/monitor_loader.py`):

- A missing `node_name` defaults to the monitor name for `[hierarchy]`, and to
  `<monitor name>-<hierarchy name>` for each `[hierarchy.X]` alternate.
- A section that declares neither `node_name` nor `parent_node` (a bare
  section) raises `ValueError`, so the file is reported as a load error - a
  root node must name itself.
- A `parent_node` equal to the hierarchy's resolved `node_name` is recorded as
  a load warning via `_record_error` and the `parent_node` is dropped, so the
  node resolves as a root. `HierarchyResolver`'s own self-parent check is left
  in place as a safety net but is now unreachable through the loader.
- No new logging of the resolved `node_name` was added; `_build_hierarchies`
  became an instance method to reach `_record_error`.

Docs: `docs/monitors.md` hierarchy table updated. Tests added to
`tests/unit/test_monitor_loader.py` covering the primary default, the alternate
default, explicit override, bare primary/alternate load failure, and the
self-referencing parent warning.