"""
Order book data model.

An "order" is a customer reservation for a configured vehicle. Each record
captures the configuration choices, the production/delivery status, and a
derived natural-language "description" used for semantic search.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    RESERVED = "reserved"          # deposit placed, not yet locked
    LOCKED = "locked"              # configuration locked, queued for production
    IN_PRODUCTION = "in_production"
    BUILT = "built"                # built, awaiting transport
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Drivetrain(str, Enum):
    RWD = "rwd"
    AWD = "awd"
    QUAD_MOTOR = "quad_motor"


class Order(BaseModel):
    order_id: str = Field(..., description="Unique order id, e.g. 'ORD-000123'")
    customer_name: str
    customer_region: str = Field(..., description="e.g. 'CA', 'NY', 'TX'")

    model: str = Field(..., description="e.g. 'R1T', 'Model 3', 'Mustang Mach-E'")
    trim: str = Field(..., description="e.g. 'Adventure', 'Long Range', 'GT'")
    exterior_color: str
    interior_color: str
    wheels: str
    drivetrain: Drivetrain
    battery_pack: str = Field(..., description="e.g. 'Standard', 'Large', 'Max'")
    tow_package: bool = False
    autopilot: bool = False
    premium_audio: bool = False

    msrp_usd: int = Field(..., ge=0)
    deposit_usd: int = Field(..., ge=0)

    status: OrderStatus
    order_date: date
    estimated_delivery: Optional[date] = None
    delivered_date: Optional[date] = None
    notes: str = ""

    def to_search_document(self) -> str:
        """A flat natural-language description used as the embedding source.

        We deliberately include structured fields verbatim so the embedding
        captures exact attribute matches (e.g. color names, trim names) as
        well as the free-form notes.
        """
        options = []
        if self.tow_package:
            options.append("tow package")
        if self.autopilot:
            options.append("autopilot / driver assist")
        if self.premium_audio:
            options.append("premium audio")
        options_str = ", ".join(options) if options else "no add-ons"

        lines = [
            f"Order {self.order_id} for {self.customer_name} in {self.customer_region}.",
            f"Vehicle: {self.model} {self.trim} in {self.exterior_color} over {self.interior_color}.",
            f"Drivetrain: {self.drivetrain.value}, {self.battery_pack} battery, {self.wheels} wheels.",
            f"Options: {options_str}.",
            f"Pricing: MSRP ${self.msrp_usd:,}, deposit ${self.deposit_usd:,}.",
            f"Status: {self.status.value}.",
            f"Ordered {self.order_date.isoformat()}",
        ]
        if self.estimated_delivery:
            lines.append(f"with estimated delivery {self.estimated_delivery.isoformat()}.")
        if self.delivered_date:
            lines.append(f"Delivered {self.delivered_date.isoformat()}.")
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        return " ".join(lines)
