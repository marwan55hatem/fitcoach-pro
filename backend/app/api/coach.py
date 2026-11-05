"""The coach chat (streamed), its conversations, and what the coach remembers."""

import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Any

import anyio
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from app.ai.client import AiClient, get_ai_client
from app.ai.coach import (
    CoachContext,
    Delta,
    Finished,
    Proposal,
    SessionFactory,
    ToolUse,
    run_coach,
    summarize,
)
from app.ai.errors import AiError
from app.ai.types import AssistantMessage, UserMessage
from app.ai.types import Message as AiMessage
from app.api.deps import DbSession, HealthUser, require_consent
from app.core.rate_limit import ai_limit
from app.db.session import get_session_factory
from app.models import CoachNote, Conversation, Message, User
from app.models.enums import ConsentType, FileKind, MessageRole
from app.rules.coach_notes import check_note
from app.rules.redflags import detect, safe_reply
from app.services import files, onboarding
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/coach", tags=["coach"])

AiDep = Annotated[AiClient, Depends(get_ai_client)]
StorageDep = Annotated[Storage, Depends(get_storage)]
FactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
AiConsent = Annotated[User, Depends(require_consent(ConsentType.AI_PROCESSING))]

RECENT_MESSAGES = 12  # sent in full; older ones live in the rolling summary
SUMMARIZE_AFTER = 24  # unsummarized messages before the oldest are folded in
CHAT_ATTACHMENTS = {FileKind.CHAT, FileKind.FOOD}  # never body progress photos

ChatText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class ChatIn(BaseModel):
    text: ChatText
    conversation_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None  # an uploaded chat or food photo


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    updated_at: dt.datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: dt.datetime
    file_id: uuid.UUID | None
    red_flag: str | None
    partial: bool


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class NoteIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)


class NoteOut(BaseModel):
    id: uuid.UUID
    key: str
    value: str
    source: str  # "ai" (the coach saved it) or "user"
    updated_at: dt.datetime


def _sse(event: str, data: dict[str, Any]) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


def _conversation(
    db: DbSession, user: User, conversation_id: uuid.UUID
) -> Conversation:
    found = db.scalar(
        sa.select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if found is None:
        raise _not_found()
    return found


def _messages(db: DbSession, conversation: Conversation) -> list[Message]:
    return list(
        db.scalars(
            sa.select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.id)
        )
    )


def _as_ai(message: Message) -> AiMessage:
    if message.role is MessageRole.USER:
        return UserMessage(message.content)
    return AssistantMessage(message.content)


async def _stream(events: Iterator[str]) -> AsyncIterator[str]:
    """Drive a blocking generator from the event loop. If the client goes away, it is
    closed (in a shielded scope) so it can save what was said so far."""
    done = object()
    try:
        while True:
            item = await run_in_threadpool(next, events, done)
            if item is done:
                return
            yield item  # type: ignore[misc]
    finally:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(events.close)


def _streaming(events: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        _stream(events),
        media_type="text/event-stream",
        headers={"cache-control": "no-store, no-transform", "x-accel-buffering": "no"},
    )


@router.post("/chat")
@ai_limit
def chat(
    request: Request,
    body: ChatIn,
    db: DbSession,
    user: HealthUser,
    _ai_consent: AiConsent,
    ai: AiDep,
    storage: StorageDep,
    sessions: FactoryDep,
) -> StreamingResponse:
    """Chat with the coach. Answers stream as server-sent events: `meta`, `delta`,
    `tool`, `proposal`, `done` and `error`. Red flags are answered before any AI call."""
    user_id = user.id
    profile = onboarding.get_profile(db, user_id)
    language = (
        profile.language if profile and profile.language in ("en", "ar") else "en"
    )

    conversation = (
        _conversation(db, user, body.conversation_id)
        if body.conversation_id
        else Conversation(user_id=user_id, title=body.text[:60])
    )
    history = _messages(db, conversation) if body.conversation_id else []
    image = None
    if body.file_id is not None:
        try:
            stored = files.get_owned(db, user_id, body.file_id)
        except files.StoredFileNotFoundError:
            raise _not_found() from None
        if stored.kind not in CHAT_ATTACHMENTS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "attachment_not_allowed",
                    "message": "Only chat or food photos can be sent to the coach. "
                    "Body progress photos are never shared with the AI.",
                },
            )

    flag = detect(body.text)
    if flag is None:
        ai.ensure_available(user_id)  # a clean error before anything streams

    now = dt.datetime.now(dt.UTC)
    conversation.updated_at = now
    db.add(conversation)
    question = Message(
        user_id=user_id,
        conversation=conversation,
        role=MessageRole.USER,
        content=body.text,
        file_id=body.file_id,
        red_flag=flag.category.value if flag else None,
        created_at=now,  # explicit: the database clock is the same for one transaction
    )
    db.add(question)
    if flag is not None:
        # A fixed, safe answer; the model is never called for this message.
        conversation.safety_mode = flag.category.value
        answer = Message(
            user_id=user_id,
            conversation=conversation,
            role=MessageRole.ASSISTANT,
            content=safe_reply(flag.category, language),
            red_flag=flag.category.value,
            created_at=now + dt.timedelta(milliseconds=1),
        )
        db.add(answer)
        db.commit()
        meta = {"conversation_id": conversation.id, "red_flag": flag.category.value}
        content = answer.content
        answer_id = answer.id

        def fixed() -> Iterator[str]:
            yield _sse("meta", meta)
            yield _sse("delta", {"text": content})
            yield _sse("done", {"message_id": answer_id, "red_flag": meta["red_flag"]})

        return _streaming(fixed())

    state = onboarding.onboarding_state(db, user)
    ctx_args = {
        "language": language,
        "today": onboarding.user_today(db, user),
        "risk_level": state.result.risk_level if state.result else None,
        "training_allowed": state.access.allowed,
        "diet_numbers_allowed": state.access.numeric_diet_targets,
        "safety_mode": conversation.safety_mode,
    }
    db.commit()  # the request's connection is released before any AI call
    conversation_id = conversation.id
    if body.file_id is not None:
        image = files.load_for_ai(db, storage, user_id, body.file_id)

    def events() -> Iterator[str]:
        yield _sse("meta", {"conversation_id": conversation_id})
        summary, recent = _rolling_summary(
            ai, sessions, user_id, conversation_id, history
        )
        message = UserMessage(body.text, images=(image,) if image else ())
        said: list[str] = []
        finished: Finished | None = None
        try:
            for event in run_coach(
                ai,
                sessions,
                user_id,
                ctx=CoachContext(summary=summary, **ctx_args),
                history=[_as_ai(m) for m in recent],
                message=message,
            ):
                if isinstance(event, Delta):
                    said.append(event.text)
                    yield _sse("delta", {"text": event.text})
                elif isinstance(event, ToolUse):
                    yield _sse("tool", {"name": event.name})
                elif isinstance(event, Proposal):
                    yield _sse("proposal", event.card)
                else:
                    finished = event
        except AiError as exc:
            yield _sse("error", {"code": exc.code, "message": exc.user_message})
        finally:
            text = finished.text if finished else "".join(said)
            if text:
                with sessions() as session:
                    reply = Message(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        role=MessageRole.ASSISTANT,
                        content=text,
                        tool_calls=finished.tool_calls if finished else None,
                        tokens_in=finished.usage.input_tokens if finished else None,
                        tokens_out=finished.usage.output_tokens if finished else None,
                        partial=finished is None,
                        created_at=dt.datetime.now(dt.UTC),
                    )
                    session.add(reply)
                    session.commit()
                    reply_id = reply.id
            else:
                reply_id = None
        if finished is not None:
            yield _sse("done", {"message_id": reply_id})

    return _streaming(events())


def _rolling_summary(
    ai: AiClient,
    sessions: SessionFactory,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    history: list[Message],
) -> tuple[str | None, list[Message]]:
    """Fold old messages into the summary when there are many; keep the last ones."""
    with sessions() as db:
        conversation = db.get(Conversation, conversation_id)
        summary = conversation.summary if conversation else None
        done = conversation.summarized_count if conversation else 0
    if len(history) - done <= SUMMARIZE_AFTER:
        return summary, history[max(done, len(history) - RECENT_MESSAGES * 2) :]
    cut = len(history) - RECENT_MESSAGES
    lines = [f"{m.role.value}: {m.content}" for m in history[done:cut]]
    try:
        summary = summarize(ai, user_id, summary, lines)
    except AiError:
        return summary, history[-RECENT_MESSAGES:]  # carry on without a new summary
    with sessions() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.summary = summary
            conversation.summarized_count = cut
            db.commit()
    return summary, history[cut:]


# --- conversations ----------------------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(db: DbSession, user: HealthUser) -> list[ConversationOut]:
    rows = db.scalars(
        sa.select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return [
        ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at) for c in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(
    conversation_id: uuid.UUID, db: DbSession, user: HealthUser
) -> ConversationDetail:
    found = _conversation(db, user, conversation_id)
    return ConversationDetail(
        id=found.id,
        title=found.title,
        updated_at=found.updated_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                file_id=m.file_id,
                red_flag=m.red_flag,
                partial=m.partial,
            )
            for m in _messages(db, found)
        ],
    )


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_conversation(
    conversation_id: uuid.UUID, db: DbSession, user: HealthUser
) -> Response:
    db.delete(_conversation(db, user, conversation_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- what the coach remembers -----------------------------------------------------------


def _note_out(note: CoachNote) -> NoteOut:
    return NoteOut(
        id=note.id,
        key=note.key,
        value=note.value,
        source=note.source,
        updated_at=note.updated_at,
    )


@router.get("/notes", response_model=list[NoteOut])
def notes(db: DbSession, user: HealthUser) -> list[NoteOut]:
    rows = db.scalars(
        sa.select(CoachNote).where(CoachNote.user_id == user.id).order_by(CoachNote.key)
    )
    return [_note_out(n) for n in rows]


@router.put("/notes", response_model=NoteOut)
def put_note(body: NoteIn, db: DbSession, user: HealthUser) -> NoteOut:
    """Add or change a note (by key). The same checks apply as to the coach's notes."""
    problem = check_note(body.key, body.value)
    if problem is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": f"note_{problem}", "message": "This note can't be saved."},
        )
    note = db.scalar(
        sa.select(CoachNote).where(
            CoachNote.user_id == user.id, CoachNote.key == body.key
        )
    )
    if note is None:
        note = CoachNote(user_id=user.id, key=body.key, value=body.value, source="user")
        db.add(note)
    else:
        note.value, note.source = body.value, "user"
    db.commit()
    return _note_out(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: uuid.UUID, db: DbSession, user: HealthUser) -> Response:
    note = db.scalar(
        sa.select(CoachNote).where(
            CoachNote.id == note_id, CoachNote.user_id == user.id
        )
    )
    if note is None:
        raise _not_found()
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
