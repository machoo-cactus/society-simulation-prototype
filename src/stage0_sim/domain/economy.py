from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stage0_sim.domain.world.model import Coordinate


@dataclass(frozen=True, slots=True)
class ItemDefinition:
    id: str
    name: str
    unit: str

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.unit:
            raise ValueError("item id, name, and unit must not be empty")


@dataclass(frozen=True, slots=True)
class ItemAmount:
    item_id: str
    quantity: int

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("item amount item_id must not be empty")
        if self.quantity <= 0:
            raise ValueError("item amount quantity must be greater than zero")


@dataclass(frozen=True, slots=True)
class TransactionOffer:
    id: str
    name: str
    character_gives: tuple[ItemAmount, ...]
    character_receives: tuple[ItemAmount, ...]
    duration: float

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("transaction offer id and name must not be empty")
        if not self.character_gives and not self.character_receives:
            raise ValueError("transaction offer must transfer at least one item")
        if self.duration <= 0:
            raise ValueError("transaction offer duration must be greater than zero")
        for side_name, amounts in (
            ("character_gives", self.character_gives),
            ("character_receives", self.character_receives),
        ):
            item_ids = [amount.item_id for amount in amounts]
            if len(item_ids) != len(set(item_ids)):
                raise ValueError(
                    f"transaction offer {self.id} has duplicate {side_name} items"
                )


class TransactionOperation(StrEnum):
    AUTOMATED = "AUTOMATED"
    STAFFED = "STAFFED"


@dataclass(frozen=True, slots=True)
class TransactionStaffing:
    role_id: str
    staff_position: Coordinate
    request_timeout: float = 60.0

    def __post_init__(self) -> None:
        if not self.role_id:
                raise ValueError("transaction staffing role_id must not be empty")
        if self.request_timeout <= 0:
                raise ValueError(
                    "transaction staffing request_timeout must be greater than zero"
                )


@dataclass(frozen=True, slots=True)
class TransactionPoint:
    id: str
    name: str
    position: Coordinate
    offers: tuple[TransactionOffer, ...]
    available: bool = True
    capacity: int = 1
    operation: TransactionOperation = TransactionOperation.AUTOMATED
    staffing: TransactionStaffing | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("transaction point id and name must not be empty")
        if not self.offers:
            raise ValueError(
                f"transaction point {self.id} must expose at least one offer"
            )
        if self.capacity <= 0:
            raise ValueError("transaction point capacity must be greater than zero")
        if self.operation is TransactionOperation.STAFFED:
            if self.staffing is None:
                raise ValueError(
                    f"staffed transaction point {self.id} requires staffing"
                )
            distance = (
                abs(self.position.x - self.staffing.staff_position.x)
                + abs(self.position.y - self.staffing.staff_position.y)
            )
            if distance != 1:
                raise ValueError(
                    f"staffed transaction point {self.id} staff position "
                    "must be adjacent to the customer position"
                )
        elif self.staffing is not None:
            raise ValueError(
                f"automated transaction point {self.id} must not define staffing"
            )
        offer_ids = [offer.id for offer in self.offers]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError(
                f"transaction point {self.id} offer IDs must be unique"
            )

    def offer(self, offer_id: str) -> TransactionOffer:
        try:
            return next(offer for offer in self.offers if offer.id == offer_id)
        except StopIteration as error:
            raise KeyError(
                f"transaction point {self.id} has no offer {offer_id}"
            ) from error


@dataclass(frozen=True, slots=True)
class ItemCatalog:
    items: tuple[ItemDefinition, ...] = ()

    def __post_init__(self) -> None:
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("item catalog IDs must be unique")

    def item(self, item_id: str) -> ItemDefinition:
        try:
            return next(item for item in self.items if item.id == item_id)
        except StopIteration as error:
            raise KeyError(f"unknown item: {item_id}") from error


def validated_holdings(holdings: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item_id, quantity in holdings.items():
        if not item_id:
            raise ValueError("holding item IDs must not be empty")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError("holding quantities must be integers")
        if quantity < 0:
            raise ValueError("holding quantities must not be negative")
        if quantity:
            result[item_id] = quantity
    return result


def can_debit(
    holdings: dict[str, int],
    amounts: tuple[ItemAmount, ...],
) -> bool:
    return all(
        holdings.get(amount.item_id, 0) >= amount.quantity
        for amount in amounts
    )


def apply_exchange(
    character_holdings: dict[str, int],
    point_holdings: dict[str, int],
    offer: TransactionOffer,
) -> None:
    if not can_debit(character_holdings, offer.character_gives):
        raise ValueError("character holdings cannot satisfy offer")
    if not can_debit(point_holdings, offer.character_receives):
        raise ValueError("transaction point holdings cannot satisfy offer")
    _apply_side(character_holdings, offer.character_gives, -1)
    _apply_side(point_holdings, offer.character_gives, 1)
    _apply_side(point_holdings, offer.character_receives, -1)
    _apply_side(character_holdings, offer.character_receives, 1)


def _apply_side(
    holdings: dict[str, int],
    amounts: tuple[ItemAmount, ...],
    direction: int,
) -> None:
    for amount in amounts:
        quantity = holdings.get(amount.item_id, 0) + direction * amount.quantity
        if quantity < 0:
            raise ValueError("exchange would create negative holdings")
        if quantity:
            holdings[amount.item_id] = quantity
        else:
            holdings.pop(amount.item_id, None)


@dataclass(slots=True)
class TransactionPointState:
    holdings: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.holdings = validated_holdings(self.holdings)


@dataclass(slots=True)
class TransactionPointRegistry:
    states: dict[str, TransactionPointState]

    def state(self, point_id: str) -> TransactionPointState:
        try:
            return self.states[point_id]
        except KeyError as error:
            raise KeyError(f"unknown transaction point: {point_id}") from error
