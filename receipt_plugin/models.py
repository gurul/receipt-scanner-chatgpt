from __future__ import annotations

from datetime import date
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Text = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
Amount = Annotated[FiniteFloat, Field(ge=0)]
Positive = Annotated[FiniteFloat, Field(gt=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReceiptItem(StrictModel):
    raw_description: Text
    quantity: Positive
    unit: Text
    unit_price: Amount | None = None
    total_price: Amount

    @model_validator(mode="after")
    def consistent_price(self):
        if self.unit_price is not None and abs(self.quantity * self.unit_price - self.total_price) > .03:
            raise ValueError("Unit price times quantity must match line total within $0.03")
        return self


class Receipt(StrictModel):
    merchant: Text
    date: date
    receipt_id: Text | None = None
    items: Annotated[list[ReceiptItem], Field(min_length=1, max_length=500)]
    subtotal: Amount
    tax: Amount = 0
    total: Amount

    @model_validator(mode="after")
    def reconciles(self):
        if abs(sum(item.total_price for item in self.items) - self.subtotal) > .02:
            raise ValueError("Line totals must match subtotal within $0.02; resolve extraction first")
        if abs(self.subtotal + self.tax - self.total) > .02:
            raise ValueError("Subtotal plus tax must match total within $0.02; resolve adjustments first")
        return self


class Alias(StrictModel):
    patterns: Annotated[list[Text], Field(min_length=1, max_length=100)]
    category: Text
    default_unit: Text


class Items(StrictModel):
    aliases: dict[Text, Alias]
    categories: list[Text] = Field(default_factory=list)


class Usage(StrictModel):
    qty: Positive
    unit: Text


class Recipe(StrictModel):
    size: str = ""
    batch_size: Positive
    ingredients: dict[Text, Usage] = Field(min_length=1)


class Products(StrictModel):
    unit_name: Text = "unit"
    recipes: dict[Text, Recipe]


class OverheadCategory(StrictModel):
    name: Text
    monthly_amount: Amount


class Overhead(StrictModel):
    monthly_production: Positive
    cost_categories: list[OverheadCategory] = Field(default_factory=list)


class TierPrice(StrictModel):
    per_unit: Positive | None = None
    low: Positive | None = None
    high: Positive | None = None

    @model_validator(mode="after")
    def ranges(self):
        if self.per_unit is None and (self.low is None or self.high is None):
            raise ValueError("Supply per_unit or both low and high prices")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("Low price must not exceed high price")
        return self


class Tier(StrictModel):
    label: Text
    prices: dict[Text, TierPrice]


class Pricing(StrictModel):
    tiers: dict[Text, Tier]


class BusinessConfig(StrictModel):
    business: dict = Field(default_factory=dict)
    items: Items
    products: Products
    overhead: Overhead
    pricing: Pricing
    extraction: dict = Field(default_factory=dict)
    sheets: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_names(self):
        for recipe in self.products.recipes.values():
            if set(recipe.ingredients) - set(self.items.aliases):
                raise ValueError("Every recipe ingredient must have a canonical item alias")
        for tier in self.pricing.tiers.values():
            if set(tier.prices) - set(self.products.recipes):
                raise ValueError("Pricing must reference configured products")
        tabs = self.sheets.get("tab_names", {})
        defaults = {"purchases": "Purchases", "latest_prices": "Latest Prices", "recipes": "Recipes",
                    "margins": "Margins", "unmapped": "Unmapped Items"}
        names = list({**defaults, **tabs}.values())
        if any(not isinstance(n, str) or not n.strip() or len(n) > 31 or
               any(c in n for c in "[]:*?/\\") for n in names):
            raise ValueError("Workbook tab names must be valid and at most 31 characters")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("Workbook tab names must be unique")
        return self

    def engine_dict(self):
        return self.model_dump(exclude_none=True)
