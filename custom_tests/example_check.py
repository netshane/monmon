"""Example custom test module.

Referenced from a monitor as:

    [test.custom.example_check]
    key = "example.custom.disk"
    command = "check_disk_space"
    args = ["/", "90", "{{>today<}}"]

A command returns either a dict (an alert), a list (report rows), or None
when everything is fine.
"""

from __future__ import annotations

import shutil


def check_disk_space(
    path: str = "/", max_percent_used: str | float = 90, as_of: str = ""
):
    """Alert when the filesystem holding `path` is fuller than the threshold."""
    usage = shutil.disk_usage(path)
    percent_used = round(usage.used / usage.total * 100, 2)
    threshold = float(max_percent_used)

    if percent_used <= threshold:
        return None

    return {
        "name": path,
        "message": f"{path} is {percent_used}% full (threshold {threshold}%)",
        "value": percent_used,
        "threshold": threshold,
        "details": f"checked {as_of or 'now'}",
    }


def disk_report(*paths: str):
    """Report free space for each supplied path."""
    rows = []
    for path in paths or ("/",):
        usage = shutil.disk_usage(path)
        rows.append(
            {
                "Path": path,
                "TotalGB": round(usage.total / 1024**3, 2),
                "FreeGB": round(usage.free / 1024**3, 2),
                "PercentUsed": round(usage.used / usage.total * 100, 2),
            }
        )

    return rows
