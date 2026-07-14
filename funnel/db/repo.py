"""Data-access helpers. Every call opens a short-lived session and returns
detached objects (safe because models carry no lazy relationships)."""

from __future__ import annotations

from typing import Optional, Type, TypeVar

from sqlmodel import select

from funnel.db import models as m
from funnel.db.engine import new_session

T = TypeVar("T")


# --- generic CRUD ---------------------------------------------------------

def add(obj: T) -> T:
    with new_session() as s:
        s.add(obj)
        s.commit()
        s.refresh(obj)
    return obj


def update(obj: T) -> T:
    """Persist changes to a detached object (upsert by primary key)."""
    with new_session() as s:
        merged = s.merge(obj)
        s.commit()
        s.refresh(merged)
    return merged


def get(model: Type[T], obj_id: str) -> Optional[T]:
    with new_session() as s:
        return s.get(model, obj_id)


def delete(model: Type[T], obj_id: str) -> None:
    with new_session() as s:
        obj = s.get(model, obj_id)
        if obj is not None:
            s.delete(obj)
            s.commit()


def list_for_request(model: Type[T], request_id: str) -> list[T]:
    with new_session() as s:
        return list(s.exec(select(model).where(model.request_id == request_id)))


# --- research requests ----------------------------------------------------

def list_requests() -> list[m.ResearchRequest]:
    with new_session() as s:
        return list(s.exec(select(m.ResearchRequest).order_by(m.ResearchRequest.created_at)))


def create_request(title: str) -> m.ResearchRequest:
    return add(m.ResearchRequest(title=title))


def delete_request(request_id: str) -> None:
    """Delete a request and every entity that belongs to it."""
    with new_session() as s:
        for model in m.CHILD_MODELS:
            for child in s.exec(select(model).where(model.request_id == request_id)):
                s.delete(child)
        req = s.get(m.ResearchRequest, request_id)
        if req is not None:
            s.delete(req)
        s.commit()


# --- intake answers -------------------------------------------------------

def get_intake_answers(request_id: str) -> dict[str, m.IntakeAnswer]:
    """Map of question_id -> stored answer for a request."""
    with new_session() as s:
        rows = s.exec(select(m.IntakeAnswer).where(m.IntakeAnswer.request_id == request_id))
        return {r.question_id: r for r in rows}


def save_intake_answer(
    request_id: str, section: str, question_id: str, question_text: str, value: str
) -> None:
    with new_session() as s:
        existing = s.exec(
            select(m.IntakeAnswer).where(
                m.IntakeAnswer.request_id == request_id,
                m.IntakeAnswer.question_id == question_id,
            )
        ).first()
        if existing is not None:
            existing.section = section
            existing.question_text = question_text
            existing.answer_value = value
            s.add(existing)
        else:
            s.add(
                m.IntakeAnswer(
                    request_id=request_id,
                    section=section,
                    question_id=question_id,
                    question_text=question_text,
                    answer_value=value,
                )
            )
        s.commit()


def count_for_request(model: Type[T], request_id: str) -> int:
    with new_session() as s:
        return len(list(s.exec(select(model).where(model.request_id == request_id))))


# --- research proposals ---------------------------------------------------

def list_proposals(request_id: str, run_id: Optional[str] = None,
                   status: Optional[str] = None) -> list[m.Proposal]:
    with new_session() as s:
        query = select(m.Proposal).where(m.Proposal.request_id == request_id)
        if run_id:
            query = query.where(m.Proposal.run_id == run_id)
        if status:
            query = query.where(m.Proposal.status == status)
        return list(s.exec(query.order_by(m.Proposal.created_at)))


def set_proposal_status(proposal_id: str, status: str) -> None:
    with new_session() as s:
        proposal = s.get(m.Proposal, proposal_id)
        if proposal is not None:
            proposal.status = status
            s.add(proposal)
            s.commit()


def latest_run(request_id: str) -> Optional[m.ResearchRun]:
    with new_session() as s:
        runs = list(s.exec(
            select(m.ResearchRun).where(m.ResearchRun.request_id == request_id)
            .order_by(m.ResearchRun.started_at)
        ))
        return runs[-1] if runs else None
