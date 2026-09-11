"""Persistence for monitor run history and last run times.

The schema is plain SQLAlchemy Core so the same code works against sqlite
today and postgres later - only the connection string in `settings.toml`
changes.  Nothing here is sqlite specific.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine

from .monitor_results import MonitorResult, ResultStatus

metadata = MetaData()

monitor_runs = Table(
    "monitor_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("monitor_name", String(255), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=True),
    Column("duration_seconds", Float, nullable=True),
    Column("message", Text, nullable=True),
    Column("alert_count", Integer, nullable=False, default=0),
    Column("report_count", Integer, nullable=False, default=0),
    Column("error_count", Integer, nullable=False, default=0),
)

monitor_test_results = Table(
    "monitor_test_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False, index=True),
    Column("monitor_name", String(255), nullable=False, index=True),
    Column("test_key", String(512), nullable=False, index=True),
    Column("test_type", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("message", Text, nullable=True),
    Column("value", Text, nullable=True),
    Column("error", Text, nullable=True),
    Column("duration_seconds", Float, nullable=True),
    Column("result_json", Text, nullable=True),
    Column("recorded_at", DateTime, nullable=False),
)

monitor_state = Table(
    "monitor_state",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("monitor_name", String(255), nullable=False),
    Column("last_run_at", DateTime, nullable=True),
    Column("last_status", String(32), nullable=True),
    Column("last_run_id", Integer, nullable=True),
    Column("next_run_at", DateTime, nullable=True),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("monitor_name", name="uq_monitor_state_monitor_name"),
)

# one row per monitor per contact type, so the renotify delays in
# `notify_throttle.py` survive between `run-scheduled` invocations.
# `last_status` is the monitor status at the time of that delivery - the alert
# and error delays reset when it changes.
notification_state = Table(
    "notification_state",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("monitor_name", String(255), nullable=False, index=True),
    Column("contact_type", String(32), nullable=False),
    Column("last_sent_at", DateTime, nullable=False),
    Column("last_status", String(32), nullable=True),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint(
        "monitor_name", "contact_type", name="uq_notification_state_monitor_type"
    ),
)

# one row per recipient per successful email, so the send quota in
# `email_quota.py` survives between `run-scheduled` invocations
email_send_log = Table(
    "email_send_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recipient", String(320), nullable=False, index=True),
    Column("monitor_name", String(255), nullable=True),
    Column("subject", Text, nullable=True),
    Column("sent_at", DateTime, nullable=False, index=True),
)


class MonitorRepository:
    """Reads and writes monitor run history."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def create_schema(self):
        """Create any missing tables."""
        metadata.create_all(self.engine)

    def save_result(self, result: MonitorResult) -> int:
        """Persist a monitor result and update the monitor's last run state.

        Returns the id of the stored run.
        """
        finished_at = result.finished_at or datetime.now()

        with self.engine.begin() as conn:
            run_id = conn.execute(
                insert(monitor_runs).values(
                    monitor_name=result.monitor_name,
                    status=result.status.value,
                    started_at=result.started_at,
                    finished_at=finished_at,
                    duration_seconds=result.duration_seconds,
                    message=result.message,
                    alert_count=len(result.alerts),
                    report_count=len(result.reports),
                    error_count=len(result.errors),
                )
            ).inserted_primary_key[0]

            for test_result in result.test_results:
                conn.execute(
                    insert(monitor_test_results).values(
                        run_id=run_id,
                        monitor_name=result.monitor_name,
                        test_key=test_result.key,
                        test_type=test_result.test_type,
                        status=test_result.status.value,
                        message=test_result.message,
                        value=None
                        if test_result.value is None
                        else str(test_result.value),
                        error=test_result.error,
                        duration_seconds=test_result.duration_seconds,
                        result_json=json.dumps(test_result.to_dict(), default=str),
                        recorded_at=finished_at,
                    )
                )

            self._upsert_state(
                conn,
                monitor_name=result.monitor_name,
                values={
                    "last_run_at": finished_at,
                    "last_status": result.status.value,
                    "last_run_id": run_id,
                    "updated_at": datetime.now(),
                },
            )

        result.run_id = run_id
        return run_id

    def set_next_run(self, monitor_name: str, next_run_at: datetime | None):
        """Record the calculated next run time for a monitor."""
        with self.engine.begin() as conn:
            self._upsert_state(
                conn,
                monitor_name=monitor_name,
                values={"next_run_at": next_run_at, "updated_at": datetime.now()},
            )

    def get_last_run(self, monitor_name: str) -> datetime | None:
        state = self.get_state(monitor_name)
        return state.get("last_run_at") if state else None

    def get_last_status(self, monitor_name: str) -> ResultStatus | None:
        state = self.get_state(monitor_name)
        if not state or not state.get("last_status"):
            return None

        try:
            return ResultStatus(state["last_status"])
        except ValueError:
            return None

    def get_state(self, monitor_name: str) -> dict | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(monitor_state).where(
                        monitor_state.c.monitor_name == monitor_name
                    )
                )
                .mappings()
                .first()
            )

        return dict(row) if row else None

    def get_all_state(self) -> dict[str, dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(monitor_state)).mappings().all()

        return {row["monitor_name"]: dict(row) for row in rows}

    def consecutive_failure_counts(self) -> dict[str, int]:
        """How many runs each monitor has had since it last ran `ok`.

        This is the state behind `recheck_max_attempts` - deriving it from
        `monitor_runs` keeps the retry cap working without adding a column to
        `monitor_state`, and it resets by itself as soon as a monitor recovers.
        Monitors whose latest run was `ok` are absent from the result.
        Note that `purge` removing an old `ok` run, and `--simulate` storing
        no runs at all, both feed into this count.
        """
        last_ok = (
            select(
                monitor_runs.c.monitor_name,
                func.max(monitor_runs.c.id).label("last_ok_id"),
            )
            .where(monitor_runs.c.status == ResultStatus.OK.value)
            .group_by(monitor_runs.c.monitor_name)
            .subquery()
        )

        # everything recorded after a monitor's last ok run is by definition a
        # non-ok run, so counting rows is enough - no status filter needed
        query = (
            select(
                monitor_runs.c.monitor_name,
                func.count().label("failures"),
            )
            .select_from(
                monitor_runs.outerjoin(
                    last_ok, last_ok.c.monitor_name == monitor_runs.c.monitor_name
                )
            )
            .where(
                or_(
                    last_ok.c.last_ok_id.is_(None),
                    monitor_runs.c.id > last_ok.c.last_ok_id,
                )
            )
            .group_by(monitor_runs.c.monitor_name)
        )

        with self.engine.connect() as conn:
            rows = conn.execute(query).all()

        return {row.monitor_name: int(row.failures) for row in rows}

    def get_runs(
        self,
        monitor_name: str | None = None,
        limit: int | None = 20,
        monitor_names: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """Run history, newest first.

        `monitor_name` limits to one monitor, `monitor_names` to a set of them,
        and `since` / `until` bound `started_at`.  `limit=None` returns every
        matching run.
        """
        query = select(monitor_runs).order_by(monitor_runs.c.started_at.desc())

        if monitor_name:
            query = query.where(monitor_runs.c.monitor_name == monitor_name)
        if monitor_names is not None:
            if not monitor_names:
                return []
            query = query.where(monitor_runs.c.monitor_name.in_(monitor_names))
        if since is not None:
            query = query.where(monitor_runs.c.started_at >= since)
        if until is not None:
            query = query.where(monitor_runs.c.started_at <= until)
        if limit is not None:
            query = query.limit(limit)

        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()

        return [dict(row) for row in rows]

    def get_test_results(
        self, run_id: int | None = None, run_ids: list[int] | None = None
    ) -> list[dict]:
        """Test results for a single run, or for a set of runs.

        One of `run_id` / `run_ids` is required - without a filter this would
        load every test result ever stored.
        """
        if run_id is None and run_ids is None:
            raise ValueError("get_test_results needs either run_id or run_ids")

        query = select(monitor_test_results)

        if run_id is not None:
            query = query.where(monitor_test_results.c.run_id == run_id)
        if run_ids is not None:
            if not run_ids:
                return []
            query = query.where(monitor_test_results.c.run_id.in_(run_ids))

        with self.engine.connect() as conn:
            rows = (
                conn.execute(query.order_by(monitor_test_results.c.id)).mappings().all()
            )

        return [dict(row) for row in rows]

    def get_monitor_names(self) -> list[str]:
        """Every monitor name that has run history or stored state."""
        with self.engine.connect() as conn:
            names = {
                row[0]
                for row in conn.execute(
                    select(monitor_runs.c.monitor_name).distinct()
                ).all()
            }
            names.update(
                row[0]
                for row in conn.execute(
                    select(monitor_state.c.monitor_name).distinct()
                ).all()
            )

        return sorted(names)

    def purge_runs_before(
        self, cutoff: datetime, monitor_name: str | None = None
    ) -> int:
        """Delete run history, test results, and stale state older than `cutoff`.

        Limits to a single monitor when `monitor_name` is given.  Returns the
        number of runs deleted.
        """
        with self.engine.begin() as conn:
            run_query = select(monitor_runs.c.id).where(
                monitor_runs.c.started_at < cutoff
            )
            if monitor_name:
                run_query = run_query.where(monitor_runs.c.monitor_name == monitor_name)
            old_run_ids = [row[0] for row in conn.execute(run_query).all()]

            if old_run_ids:
                conn.execute(
                    delete(monitor_test_results).where(
                        monitor_test_results.c.run_id.in_(old_run_ids)
                    )
                )
                conn.execute(
                    delete(monitor_runs).where(monitor_runs.c.id.in_(old_run_ids))
                )

            state_query = delete(monitor_state).where(
                or_(
                    monitor_state.c.last_run_at < cutoff,
                    monitor_state.c.last_run_id.in_(old_run_ids),
                )
                if old_run_ids
                else monitor_state.c.last_run_at < cutoff
            )
            if monitor_name:
                state_query = state_query.where(
                    monitor_state.c.monitor_name == monitor_name
                )
            conn.execute(state_query)

            # renotify timers belong to a monitor, not to its run history, so
            # they go only when the monitor itself has aged out of
            # `monitor_state`.  Deleting them on the run cutoff instead would
            # truncate any window longer than the purge retention and re-send
            # the notification early.
            remaining = select(monitor_state.c.monitor_name).distinct()
            throttle_query = delete(notification_state).where(
                notification_state.c.monitor_name.not_in(remaining)
            )
            if monitor_name:
                throttle_query = throttle_query.where(
                    notification_state.c.monitor_name == monitor_name
                )
            conn.execute(throttle_query)

        return len(old_run_ids)

    # -- email send log ---------------------------------------------------

    def record_email_sends(
        self,
        recipients: list[str],
        monitor_name: str | None = None,
        subject: str | None = None,
        sent_at: datetime | None = None,
    ) -> int:
        """Log one row per recipient of a delivered email.

        Written in a single transaction so a group send either counts for
        every recipient or for none of them.  Returns the number of rows.
        """
        if not recipients:
            return 0

        sent_at = sent_at or datetime.now()

        with self.engine.begin() as conn:
            for recipient in recipients:
                conn.execute(
                    insert(email_send_log).values(
                        recipient=recipient,
                        monitor_name=monitor_name,
                        subject=subject,
                        sent_at=sent_at,
                    )
                )

        return len(recipients)

    def count_email_sends_since(self, cutoff: datetime) -> int:
        """Total recipient-sends at or after `cutoff`."""
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(func.count())
                    .select_from(email_send_log)
                    .where(email_send_log.c.sent_at >= cutoff)
                ).scalar_one()
                or 0
            )

    def count_email_sends_since_by_recipient(
        self, cutoff: datetime, recipients: list[str]
    ) -> dict[str, int]:
        """`{recipient: count}` at or after `cutoff`, one query for the group.

        Recipients with no rows are absent from the map.
        """
        if not recipients:
            return {}

        query = (
            select(email_send_log.c.recipient, func.count())
            .where(email_send_log.c.sent_at >= cutoff)
            .where(email_send_log.c.recipient.in_(recipients))
            .group_by(email_send_log.c.recipient)
        )

        with self.engine.connect() as conn:
            return {row[0]: row[1] for row in conn.execute(query).all()}

    # -- notification throttle state --------------------------------------

    def get_notification_state(self, monitor_name: str) -> dict[str, dict[str, object]]:
        """Every stored renotify timer for one monitor, keyed by contact type."""
        query = select(
            notification_state.c.contact_type,
            notification_state.c.last_sent_at,
            notification_state.c.last_status,
        ).where(notification_state.c.monitor_name == monitor_name)

        with self.engine.connect() as conn:
            return {
                row[0]: {"last_sent_at": row[1], "last_status": row[2]}
                for row in conn.execute(query).all()
            }

    def record_notification(
        self,
        monitor_name: str,
        contact_type: str,
        sent_at: datetime,
        status: str | None = None,
    ):
        """Record that a notification of `contact_type` was delivered."""
        values = {
            "last_sent_at": sent_at,
            "last_status": status,
            "updated_at": datetime.now(),
        }

        with self.engine.begin() as conn:
            exists = conn.execute(
                select(func.count())
                .select_from(notification_state)
                .where(notification_state.c.monitor_name == monitor_name)
                .where(notification_state.c.contact_type == contact_type)
            ).scalar_one()

            if exists:
                conn.execute(
                    update(notification_state)
                    .where(notification_state.c.monitor_name == monitor_name)
                    .where(notification_state.c.contact_type == contact_type)
                    .values(**values)
                )
            else:
                conn.execute(
                    insert(notification_state).values(
                        monitor_name=monitor_name,
                        contact_type=contact_type,
                        **values,
                    )
                )

    def clear_notification_state(
        self, monitor_name: str, contact_types: list[str] | None = None
    ) -> int:
        """Drop stored timers for a monitor, so the next notification sends.

        Limits to `contact_types` when given - a monitor returning to `ok`
        resets its alert and error timers without touching the others.
        """
        query = delete(notification_state).where(
            notification_state.c.monitor_name == monitor_name
        )
        if contact_types is not None:
            if not contact_types:
                return 0
            query = query.where(notification_state.c.contact_type.in_(contact_types))

        with self.engine.begin() as conn:
            return conn.execute(query).rowcount

    def purge_email_send_log_before(self, cutoff: datetime) -> int:
        """Delete send log rows sent before `cutoff`.  Returns rows deleted."""
        with self.engine.begin() as conn:
            return conn.execute(
                delete(email_send_log).where(email_send_log.c.sent_at < cutoff)
            ).rowcount

    def purge_monitor(self, monitor_name: str) -> int:
        """Delete all state, run history, and test results for one monitor.

        Returns the number of runs deleted.
        """
        with self.engine.begin() as conn:
            run_ids = [
                row[0]
                for row in conn.execute(
                    select(monitor_runs.c.id).where(
                        monitor_runs.c.monitor_name == monitor_name
                    )
                ).all()
            ]

            if run_ids:
                conn.execute(
                    delete(monitor_test_results).where(
                        monitor_test_results.c.run_id.in_(run_ids)
                    )
                )
                conn.execute(delete(monitor_runs).where(monitor_runs.c.id.in_(run_ids)))

            conn.execute(
                delete(monitor_state).where(
                    monitor_state.c.monitor_name == monitor_name
                )
            )
            conn.execute(
                delete(notification_state).where(
                    notification_state.c.monitor_name == monitor_name
                )
            )

        return len(run_ids)

    @staticmethod
    def _upsert_state(conn, monitor_name: str, values: dict):
        exists = conn.execute(
            select(func.count())
            .select_from(monitor_state)
            .where(monitor_state.c.monitor_name == monitor_name)
        ).scalar_one()

        if exists:
            conn.execute(
                update(monitor_state)
                .where(monitor_state.c.monitor_name == monitor_name)
                .values(**values)
            )
        else:
            conn.execute(
                insert(monitor_state).values(monitor_name=monitor_name, **values)
            )
