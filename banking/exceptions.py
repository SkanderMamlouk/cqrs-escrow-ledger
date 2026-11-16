class LedgerError(Exception):
    pass


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
    def __init__(self, account_id, expected_version, actual_version):
        self.account_id = account_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Concurrency conflict on account {str(account_id)[:8]}: "
            f"you expected version {expected_version} but it is now at version {actual_version}. "
            "Reload the page and try again."
        )

