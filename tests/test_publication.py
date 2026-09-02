from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import pytest

from app.publication import (
    InMemoryPublicationApprovalStateStore,
    InMemoryPublicationHistoryStore,
    PublicationActionSigner,
    PublicationApprovalError,
    PublicationApprovalService,
    create_publication_draft,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def draft(owner: str = "line-1", revision: int = 1):
    return create_publication_draft(
        athlete_id="athlete-1",
        line_user_id=owner,
        provider="note",
        source_plan_version_id="plan-1",
        revision=revision,
        title="週間トレーニング",
        body="公開前の下書き",
        expires_at=NOW + timedelta(hours=1),
    )


async def test_publication_requires_signed_owner_provider_and_revision() -> None:
    signer = PublicationActionSigner(
        "separate-publication-domain", clock=lambda: NOW.timestamp()
    )
    history = InMemoryPublicationHistoryStore()
    states = InMemoryPublicationApprovalStateStore()
    service = PublicationApprovalService(states, history, signer, clock=lambda: NOW)
    item = draft()
    await service.register(item)
    values = {
        key: value[0] for key, value in parse_qs(signer.create(item, "approve")).items()
    }

    decision = await service.decide(
        draft=item,
        line_user_id="line-1",
        decision="approve",
        expires_at=int(values["expires_at"]),
        signature=values["signature"],
        approval_event_id="line-event-1",
    )

    assert decision is not None
    assert decision.provider == "note"
    assert len(history.decisions) == 1
    assert (
        await service.decide(
            draft=item,
            line_user_id="line-1",
            decision="approve",
            expires_at=int(values["expires_at"]),
            signature=values["signature"],
            approval_event_id="line-event-1",
        )
        is None
    )


async def test_publication_approval_rejects_different_owner() -> None:
    signer = PublicationActionSigner(
        "separate-publication-domain", clock=lambda: NOW.timestamp()
    )
    service = PublicationApprovalService(
        InMemoryPublicationApprovalStateStore(),
        InMemoryPublicationHistoryStore(),
        signer,
        clock=lambda: NOW,
    )
    item = draft()
    await service.register(item)
    values = {
        key: value[0] for key, value in parse_qs(signer.create(item, "approve")).items()
    }

    with pytest.raises(PublicationApprovalError, match="signature"):
        await service.decide(
            draft=item,
            line_user_id="line-2",
            decision="approve",
            expires_at=int(values["expires_at"]),
            signature=values["signature"],
            approval_event_id="line-event-2",
        )


def test_publication_revision_has_deterministic_distinct_id() -> None:
    assert draft(revision=1).id == draft(revision=1).id
    assert draft(revision=1).id != draft(revision=2).id
