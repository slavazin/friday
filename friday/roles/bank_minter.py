"""The bank minter (SPEC § 4): requests, tags, contracts, checks, stubs; validated against references."""

from __future__ import annotations

from dataclasses import dataclass

from friday.ports.models import ModelClient
from friday.roles.prompts import PromptBook

ROLE = "bank_minter"


@dataclass
class BankMinter:
    client: ModelClient
    book: PromptBook

    def mint(self) -> None:
        raise NotImplementedError("block 11:45: bank minting and check validation")
