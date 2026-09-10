from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, lazyload, selectinload

from app.models import Task


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, status: str | None = None) -> list[Task]:
        # TaskRead and SSE need only task columns, not every historical stage,
        # snapshot, text edit and export for every task on each refresh.
        query = (
            select(Task).options(lazyload("*"))
            .where(Task.deleted_at.is_(None))
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
        if status:
            query = query.where(Task.status == status.upper())
        return list(self.session.scalars(query))

    def get(self, task_id: int, *, detail: bool = False, include_deleted: bool = False) -> Task | None:
        query = select(Task).where(Task.id == task_id)
        if not include_deleted:
            query = query.where(Task.deleted_at.is_(None))
        if detail:
            query = query.options(selectinload(Task.snapshot), selectinload(Task.stages))
        return self.session.scalar(query)

    def counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(Task.status, func.count()).where(Task.deleted_at.is_(None)).group_by(Task.status)
        ).all()
        return {status: count for status, count in rows}

    def page(
        self, *, offset: int = 0, limit: int = 100, status: str | None = None,
        search: str = "", trashed: bool = False,
    ) -> tuple[list[Task], int, int]:
        filters = [Task.deleted_at.is_not(None) if trashed else Task.deleted_at.is_(None)]
        if status:
            filters.append(Task.status == status.upper())
        if search.strip():
            term = search.strip()
            filters.append(or_(Task.title.contains(term, autoescape=True),
                               Task.source.contains(term, autoescape=True)))
        total = self.session.scalar(select(func.count()).select_from(Task).where(*filters)) or 0
        offset = min(offset, max(0, (total - 1) // limit * limit))
        query = (
            select(Task).options(lazyload("*")).where(*filters)
            .order_by(Task.created_at.desc(), Task.id.desc()).offset(offset).limit(limit)
        )
        return list(self.session.scalars(query)), total, offset
