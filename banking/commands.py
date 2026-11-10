"""
Command input validation with Pydantic v2
=========================================

Anything typed by a user in a web form is UNTRUSTED.  Before a command even
reaches the aggregate, Pydantic checks its shape:  is the amount a positive
number with at most 3 decimals?  Is the account type one of the allowed
values?  Is the reference not too long?  If not, Pydantic raises a clear
ValidationError and nothing happens.

Two layers of protection:
    Pydantic (here)       -> "is the input well-formed?"
    Aggregate (domain.py) -> "is it allowed by the business rules?"
                             (e.g. enough available funds)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from banking.money import to_millimes

PositiveTND = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
Text = Annotated[str, Field(max_length=160)]


class _Command(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    expected_version: int | None = Field(default=None, ge=1)

    @property
    def millimes(self) -> int:
        return to_millimes(getattr(self, "amount"))


class OpenAccount(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    owner_name: Annotated[str, Field(min_length=2, max_length=80)]
    account_type: Literal["CURRENT", "SAVINGS", "ESCROW"] = "CURRENT"
    initial_deposit: Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=3)] = Decimal("0")

    @property
    def initial_millimes(self) -> int:
        return to_millimes(self.initial_deposit)


class Deposit(_Command):
    amount: PositiveTND
    reference: Text = ""
    channel: Literal["BRANCH", "MOBILE", "ATM", "WIRE", "CHEQUE"] = "BRANCH"


class Withdraw(_Command):
    amount: PositiveTND
    reference: Text = ""


class ReserveFunds(_Command):
    amount: PositiveTND
    beneficiary_id: UUID
    reason: Text = ""


class ReleaseHold(_Command):
    hold_id: Annotated[str, Field(pattern=r"^HLD-[0-9a-f]{8}$")]
    reason: Text = ""


class SettleEscrow(_Command):
    hold_id: Annotated[str, Field(pattern=r"^HLD-[0-9a-f]{8}$")]
    reference: Text = ""


class Transfer(_Command):
    amount: PositiveTND
    to_account_id: UUID
    reference: Text = ""


class ChargeFee(_Command):
    amount: PositiveTND
    reason: Text = ""


class ReverseFee(_Command):
    fee_id: Annotated[str, Field(pattern=r"^FEE-[0-9a-f]{8}$")]
    reason: Text = ""

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("A fee reversal needs a justification (min 3 characters).")
        return v
