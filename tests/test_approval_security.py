from urllib.parse import parse_qs

import pytest

from app.security import ApprovalActionError, ApprovalActionSigner


def test_approval_action_signature_binds_owner_decision_and_expiry() -> None:
    signer = ApprovalActionSigner("secret", clock=lambda: 100)
    data = signer.create("proposal-1", "line-1", "approve", 200)
    values = {key: items[0] for key, items in parse_qs(data).items()}

    signer.verify(
        values["proposal_id"],
        "line-1",
        values["decision"],
        values["expires_at"],
        values["signature"],
    )

    with pytest.raises(ApprovalActionError):
        signer.verify(
            values["proposal_id"],
            "line-2",
            values["decision"],
            values["expires_at"],
            values["signature"],
        )


def test_approval_action_rejects_expired_token() -> None:
    signer = ApprovalActionSigner("secret", clock=lambda: 201)
    data = signer.create("proposal-1", "line-1", "approve", 200)
    values = {key: items[0] for key, items in parse_qs(data).items()}

    with pytest.raises(ApprovalActionError, match="expired"):
        signer.verify(
            values["proposal_id"],
            "line-1",
            values["decision"],
            values["expires_at"],
            values["signature"],
        )
