from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Task


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, status: str | None = None) -> list[Task]:
        query = select(Task).order_by(Task.created_at.desc(), Task.id.desc())
        if status:
            query = query.where(Task.status == status.upper())
        return list(self.session.scalars(query))

    def get(self, task_id: int, *, detail: bool = False) -> Task | None:
        query = select(Task).where(Task.id == task_id)
        if detail:
            query = query.options(selectinload(Task.snapshot), selectinload(Task.stages))
        return self.session.scalar(query)

    def counts(self) -> dict[str, int]:
        rows = self.session.execute(select(Task.status, func.count()).group_by(Task.status)).all()
        return {status: count for status, count in rows}
