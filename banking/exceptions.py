"""Business errors raised by the write model (they never create an event)."""


class LedgerError(Exception):
    """Base class: a command was refused because it breaks a business rule."""


class InvalidAmount(LedgerError):
    pass


class InsufficientFunds(LedgerError):
    pass


class UnknownHold(LedgerError):
    pass


class UnknownFee(LedgerError):
    pass


class AccountNotFound(LedgerError):
    pass


class ConcurrencyConflict(LedgerError):
    """Optimistic concurrency control: somebody changed the account first.

    The user (or program) expected the account to be at version N, but the
    event store already contains version N+1 or more.  Nothing is written;
    the caller must reload the account and try again.
    """

    def __init__(self, account_id, expected_version, actual_version):
        self.account_id = account_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Concurrency conflict on account {str(account_id)[:8]}: "
            f"you expected version {expected_version} but it is now at version {actual_version}. "
            "Reload the page and try again."
        )
