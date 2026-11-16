from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["opening", "deposit", "withdrawal", "escrow", "transfer", "fee", "reversal", "balance", "risk"]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^EVT-[0-9a-f]{8}-\d{4,}$", description="Exact event_id copied from the evidence")
    timestamp: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                           description="Exact timestamp of that event, copied from the evidence")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    statement: str = Field(min_length=10, max_length=400)
    citations: list[Citation] = Field(min_length=1, max_length=15)


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(min_length=5, max_length=200)
    findings: list[Finding] = Field(min_length=1, max_length=12)
    closing_balance_tnd: str = Field(pattern=r"^-?\d+\.\d{3}$")


CATEGORY_EVENT_TYPES: dict[str, set[str] | None] = {
    "opening": None,
    "deposit": {"FundsDeposited", "AccountOpened"},
    "withdrawal": {"FundsWithdrawn"},
    "escrow": {"FundsReserved", "HoldReleased", "TransferSettled"},
    "transfer": {"TransferSettled"},
    "fee": {"FeeCharged", "FeeReversed"},
    "reversal": {"FeeReversed"},
    "balance": None,
    "risk": None,
}

