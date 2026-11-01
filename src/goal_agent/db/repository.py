"""ALL database queries live here. Callers never touch sessions directly."""

from __future__ import annotations

from datetime import date, datetime, timezone
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from goal_agent.models.checkin import CheckIn, CheckInRow
from goal_agent.models.event import Event, EventRow
from goal_agent.models.goal import Goal, GoalRow
from goal_agent.models.plan import Milestone, MilestoneRow, PlanVersion, Task, TaskRow, PlanVersionRow
from goal_agent.models.user import Memory, MemoryRow, UserProfile, UserProfileRow


def _row_to_user(r: UserProfileRow) -> UserProfile:
    return UserProfile(
        user_id=r.user_id,
        name=r.name,
        daily_hours_available=r.daily_hours_available,
        work_days=list(r.work_days or []),
        checkin_time=r.checkin_time,
        nudge_style=r.nudge_style,  # type: ignore[arg-type]
        slippage_multiplier=r.slippage_multiplier,
    )


def _row_to_goal(r: GoalRow) -> Goal:
    return Goal(
        goal_id=r.goal_id,
        title=r.title,
        description=r.description,
        deadline=r.deadline,
        priority=r.priority,
        minimal_viable_outcome=r.minimal_viable_outcome,
        status=r.status,  # type: ignore[arg-type]
        created_at=r.created_at,
    )


def _row_to_plan(r: PlanVersionRow) -> PlanVersion:
    return PlanVersion(
        version_id=r.version_id,
        goal_id=r.goal_id,
        version_number=r.version_number,
        is_active=bool(r.is_active),
        strategy=r.strategy,
        target_finish=r.target_finish,
        buffer_days=r.buffer_days,
        projected_finish=r.projected_finish,
        user_velocity=r.user_velocity,
        risk=r.risk,  # type: ignore[arg-type]
        changelog=r.changelog,
        created_at=r.created_at,
    )


def _row_to_milestone(r: MilestoneRow) -> Milestone:
    return Milestone(
        milestone_id=r.milestone_id,
        plan_version_id=r.plan_version_id,
        name=r.name,
        target_date=r.target_date,
    )


def _row_to_task(r: TaskRow) -> Task:
    return Task(
        task_id=r.task_id,
        milestone_id=r.milestone_id,
        desc=r.desc,
        est_hours=r.est_hours,
        actual_hours=r.actual_hours,
        due_date=r.due_date,
        status=r.status,  # type: ignore[arg-type]
        is_core=bool(r.is_core),
        dependencies=list(r.dependencies or []),
    )


def _row_to_checkin(r: CheckInRow) -> CheckIn:
    return CheckIn(
        checkin_id=r.checkin_id,
        goal_id=r.goal_id,
        date=r.date,
        completed_task_ids=list(r.completed_task_ids or []),
        hours_spent=r.hours_spent,
        notes=r.notes,
    )


def _row_to_event(r: EventRow) -> Event:
    return Event(
        event_id=r.event_id,
        type=r.type,  # type: ignore[arg-type]
        description=r.description,
        start_date=r.start_date,
        end_date=r.end_date,
        created_at=r.created_at,
    )


def _row_to_memory(r: MemoryRow) -> Memory:
    return Memory(memory_id=r.memory_id, text=r.text, tag=r.tag, created_at=r.created_at)


class Repository:
    """Synchronous repository wrapping a SQLAlchemy session factory."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._factory = session_factory

    # ------------------------------------------------------------------
    # User
    # ------------------------------------------------------------------
    def get_profile(self, user_id: int = 1) -> UserProfile | None:
        with self._factory() as s:
            row = s.get(UserProfileRow, user_id)
            return _row_to_user(row) if row else None

    def get_or_create_profile(self, user_id: int = 1) -> UserProfile:
        existing = self.get_profile(user_id)
        if existing:
            return existing
        with self._factory() as s:
            row = UserProfileRow(
                user_id=user_id,
                name="",
                daily_hours_available=2.0,
                work_days=[0, 1, 2, 3, 4],
                checkin_time="09:00",
                nudge_style="encouraging",
                slippage_multiplier=1.0,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_user(row)

    def save_profile(self, profile: UserProfile) -> UserProfile:
        with self._factory() as s:
            if profile.user_id is None:
                row = UserProfileRow(
                    name=profile.name,
                    daily_hours_available=profile.daily_hours_available,
                    work_days=profile.work_days,
                    checkin_time=profile.checkin_time,
                    nudge_style=profile.nudge_style,
                    slippage_multiplier=profile.slippage_multiplier,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return _row_to_user(row)
            row = s.get(UserProfileRow, profile.user_id)
            if row is None:
                row = UserProfileRow(
                    user_id=profile.user_id,
                    name=profile.name,
                    daily_hours_available=profile.daily_hours_available,
                    work_days=profile.work_days,
                    checkin_time=profile.checkin_time,
                    nudge_style=profile.nudge_style,
                    slippage_multiplier=profile.slippage_multiplier,
                )
                s.add(row)
            else:
                row.name = profile.name
                row.daily_hours_available = profile.daily_hours_available
                row.work_days = profile.work_days
                row.checkin_time = profile.checkin_time
                row.nudge_style = profile.nudge_style
                row.slippage_multiplier = profile.slippage_multiplier
            s.commit()
            s.refresh(row)
            return _row_to_user(row)

    def update_slippage(self, user_id: int, slippage: float) -> UserProfile:
        profile = self.get_or_create_profile(user_id)
        profile.slippage_multiplier = max(1.0, min(2.0, slippage))
        return self.save_profile(profile)

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------
    def create_goal(
        self,
        title: str,
        description: str,
        deadline: date,
        priority: int,
        minimal_viable_outcome: str,
    ) -> Goal:
        with self._factory() as s:
            row = GoalRow(
                title=title,
                description=description,
                deadline=deadline,
                priority=priority,
                minimal_viable_outcome=minimal_viable_outcome,
                status="active",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_goal(row)

    def get_goal(self, goal_id: int) -> Goal | None:
        with self._factory() as s:
            row = s.get(GoalRow, goal_id)
            return _row_to_goal(row) if row else None

    def list_goals(self, status: str | None = None) -> list[Goal]:
        with self._factory() as s:
            stmt = select(GoalRow).order_by(GoalRow.created_at.asc())
            if status is not None:
                stmt = stmt.where(GoalRow.status == status)
            rows = list(s.scalars(stmt).all())
            return [_row_to_goal(r) for r in rows]

    def list_active_goals(self) -> list[Goal]:
        with self._factory() as s:
            stmt = (
                select(GoalRow)
                .where(GoalRow.status.in_(["active", "at_risk", "negotiating"]))
                .order_by(GoalRow.deadline.asc())
            )
            rows = list(s.scalars(stmt).all())
            return [_row_to_goal(r) for r in rows]

    def update_goal_status(self, goal_id: int, status: str) -> Goal | None:
        with self._factory() as s:
            row = s.get(GoalRow, goal_id)
            if row is None:
                return None
            row.status = status
            s.commit()
            s.refresh(row)
            return _row_to_goal(row)

    def update_goal_deadline(self, goal_id: int, new_deadline: date) -> Goal | None:
        with self._factory() as s:
            row = s.get(GoalRow, goal_id)
            if row is None:
                return None
            row.deadline = new_deadline
            s.commit()
            s.refresh(row)
            return _row_to_goal(row)

    # ------------------------------------------------------------------
    # Plan versions (INSERT-only)
    # ------------------------------------------------------------------
    def create_plan_version(
        self,
        goal_id: int,
        strategy: str,
        target_finish: date,
        buffer_days: int,
        projected_finish: date,
        user_velocity: float,
        risk: str,
        changelog: str,
    ) -> PlanVersion:
        """Atomically deactivate old active version and insert new one.

        Never UPDATEs an existing version's content — INSERT-only.
        """
        with self._factory() as s:
            with s.begin():
                existing = list(
                    s.scalars(
                        select(PlanVersionRow)
                        .where(
                            PlanVersionRow.goal_id == goal_id,
                            PlanVersionRow.is_active.is_(True),
                        )
                        .order_by(PlanVersionRow.version_number.desc())
                    ).all()
                )
                max_version = 0
                # Find global max version number for this goal (active or not)
                all_versions = list(
                    s.scalars(
                        select(PlanVersionRow).where(PlanVersionRow.goal_id == goal_id)
                    ).all()
                )
                if all_versions:
                    max_version = max(v.version_number for v in all_versions)
                for v in existing:
                    v.is_active = False
                    s.add(v)
                new_row = PlanVersionRow(
                    goal_id=goal_id,
                    version_number=max_version + 1,
                    is_active=True,
                    strategy=strategy,
                    target_finish=target_finish,
                    buffer_days=buffer_days,
                    projected_finish=projected_finish,
                    user_velocity=user_velocity,
                    risk=risk,
                    changelog=changelog,
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                s.add(new_row)
            s.refresh(new_row)
            return _row_to_plan(new_row)

    def get_active_plan(self, goal_id: int) -> PlanVersion | None:
        with self._factory() as s:
            stmt = (
                select(PlanVersionRow)
                .where(PlanVersionRow.goal_id == goal_id, PlanVersionRow.is_active.is_(True))
                .order_by(PlanVersionRow.version_number.desc())
                .limit(1)
            )
            row = s.scalars(stmt).first()
            return _row_to_plan(row) if row else None

    def list_plan_history(self, goal_id: int) -> list[PlanVersion]:
        with self._factory() as s:
            stmt = (
                select(PlanVersionRow)
                .where(PlanVersionRow.goal_id == goal_id)
                .order_by(PlanVersionRow.version_number.asc())
            )
            rows = list(s.scalars(stmt).all())
            return [_row_to_plan(r) for r in rows]

    def get_plan_version(self, version_id: int) -> PlanVersion | None:
        with self._factory() as s:
            row = s.get(PlanVersionRow, version_id)
            return _row_to_plan(row) if row else None

    # ------------------------------------------------------------------
    # Milestones & tasks
    # ------------------------------------------------------------------
    def create_milestone(self, plan_version_id: int, name: str, target_date: date) -> Milestone:
        with self._factory() as s:
            row = MilestoneRow(
                plan_version_id=plan_version_id, name=name, target_date=target_date
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_milestone(row)

    def list_milestones_for_plan(self, plan_version_id: int) -> list[Milestone]:
        with self._factory() as s:
            stmt = (
                select(MilestoneRow)
                .where(MilestoneRow.plan_version_id == plan_version_id)
                .order_by(MilestoneRow.target_date.asc())
            )
            rows = list(s.scalars(stmt).all())
            return [_row_to_milestone(r) for r in rows]

    def create_task(
        self,
        milestone_id: int,
        desc: str,
        est_hours: float,
        due_date: date | None = None,
        is_core: bool = True,
        dependencies: list[int] | None = None,
        status: str = "pending",
        actual_hours: float | None = None,
    ) -> Task:
        with self._factory() as s:
            row = TaskRow(
                milestone_id=milestone_id,
                desc=desc,
                est_hours=est_hours,
                actual_hours=actual_hours,
                due_date=due_date,
                status=status,
                is_core=is_core,
                dependencies=dependencies or [],
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_task(row)

    def get_task(self, task_id: int) -> Task | None:
        with self._factory() as s:
            row = s.get(TaskRow, task_id)
            return _row_to_task(row) if row else None

    def list_tasks_for_milestone(self, milestone_id: int) -> list[Task]:
        with self._factory() as s:
            stmt = select(TaskRow).where(TaskRow.milestone_id == milestone_id)
            rows = list(s.scalars(stmt).all())
            return [_row_to_task(r) for r in rows]

    def list_tasks_for_plan(self, plan_version_id: int) -> list[Task]:
        with self._factory() as s:
            ms_ids = list(
                s.scalars(
                    select(MilestoneRow.milestone_id).where(
                        MilestoneRow.plan_version_id == plan_version_id
                    )
                ).all()
            )
            if not ms_ids:
                return []
            stmt = select(TaskRow).where(TaskRow.milestone_id.in_(ms_ids))
            rows = list(s.scalars(stmt).all())
            return [_row_to_task(r) for r in rows]

    def update_task(
        self,
        task_id: int,
        status: str | None = None,
        actual_hours: float | None = None,
        due_date: date | None = None,
    ) -> Task | None:
        with self._factory() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
            if actual_hours is not None:
                row.actual_hours = actual_hours
            if due_date is not None:
                row.due_date = due_date
            s.commit()
            s.refresh(row)
            return _row_to_task(row)

    def list_tasks_with_actuals(self, limit: int = 5) -> list[Task]:
        """Most recent tasks that have actual_hours set (for velocity)."""
        with self._factory() as s:
            stmt = (
                select(TaskRow)
                .where(TaskRow.actual_hours.is_not(None))
                .order_by(TaskRow.task_id.desc())
                .limit(limit)
            )
            rows = list(s.scalars(stmt).all())
            return [_row_to_task(r) for r in rows]

    # ------------------------------------------------------------------
    # Check-ins
    # ------------------------------------------------------------------
    def create_checkin(
        self,
        goal_id: int,
        checkin_date: date,
        completed_task_ids: list[int],
        hours_spent: float,
        notes: str = "",
    ) -> CheckIn:
        with self._factory() as s:
            row = CheckInRow(
                goal_id=goal_id,
                date=checkin_date,
                completed_task_ids=completed_task_ids,
                hours_spent=hours_spent,
                notes=notes,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            # Mark completed tasks done (side effect, best-effort)
            for tid in completed_task_ids:
                trow = s.get(TaskRow, tid)
                if trow is not None:
                    trow.status = "done"
            s.commit()
            return _row_to_checkin(row)

    def list_checkins_for_goal(self, goal_id: int) -> list[CheckIn]:
        with self._factory() as s:
            stmt = (
                select(CheckInRow)
                .where(CheckInRow.goal_id == goal_id)
                .order_by(CheckInRow.date.asc())
            )
            rows = list(s.scalars(stmt).all())
            return [_row_to_checkin(r) for r in rows]

    def get_last_checkin_date(self, goal_id: int) -> date | None:
        with self._factory() as s:
            stmt = (
                select(CheckInRow)
                .where(CheckInRow.goal_id == goal_id)
                .order_by(CheckInRow.date.desc())
                .limit(1)
            )
            row = s.scalars(stmt).first()
            return row.date if row else None

    def has_checkin_on(self, goal_id: int, checkin_date: date) -> bool:
        with self._factory() as s:
            stmt = (
                select(CheckInRow)
                .where(CheckInRow.goal_id == goal_id, CheckInRow.date == checkin_date)
                .limit(1)
            )
            return s.scalars(stmt).first() is not None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def create_event(
        self,
        event_type: str,
        description: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Event:
        with self._factory() as s:
            row = EventRow(
                type=event_type,
                description=description,
                start_date=start_date,
                end_date=end_date,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_event(row)

    def list_events(self, event_type: str | None = None) -> list[Event]:
        with self._factory() as s:
            stmt = select(EventRow).order_by(EventRow.created_at.asc())
            if event_type is not None:
                stmt = stmt.where(EventRow.type == event_type)
            rows = list(s.scalars(stmt).all())
            return [_row_to_event(r) for r in rows]

    def get_blocked_dates(self) -> set[date]:
        """Expand all interruption events into a set of blocked dates (inclusive)."""
        from datetime import timedelta

        blocked: set[date] = set()
        for ev in self.list_events("interruption"):
            if ev.start_date is None:
                continue
            end = ev.end_date or ev.start_date
            cur = ev.start_date
            while cur <= end:
                blocked.add(cur)
                cur += timedelta(days=1)
        return blocked

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    def create_memory(self, text: str, tag: str | None = None) -> Memory:
        with self._factory() as s:
            row = MemoryRow(text=text, tag=tag, created_at=datetime.now(timezone.utc).replace(tzinfo=None))
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_memory(row)

    def list_memories(self) -> list[Memory]:
        with self._factory() as s:
            stmt = select(MemoryRow).order_by(MemoryRow.created_at.asc())
            rows = list(s.scalars(stmt).all())
            return [_row_to_memory(r) for r in rows]

    def search_memories(self, query: str) -> list[Memory]:
        q = query.lower()
        all_mems = self.list_memories()
        scored: list[tuple[int, Memory]] = []
        for m in all_mems:
            hay = f"{m.text} {m.tag or ''}".lower()
            if q in hay:
                # simple score: earlier occurrence ranks higher
                scored.append((hay.index(q), m))
        scored.sort(key=lambda t: t[0])
        return [m for _, m in scored]
