"""Phase 3.2 — Rate / Contract Domain Schemas."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RateType(str, Enum):
    FLAT = "flat"
    PER_MILE = "per_mile"
    PER_CWT = "per_cwt"  # per hundredweight (100 lbs)


class FuelScheduleType(str, Enum):
    PERCENT = "percent"
    TABLE = "table"
    INDEX_FORMULA = "index_formula"


class FuelSchedule(BaseModel):
    schedule_type: FuelScheduleType = FuelScheduleType.PERCENT
    base_fuel_price: float = 3.00
    base_surcharge_percent: float = 15.0
    percent_per_increment: float = 0.5
    price_increment: float = 0.05
    bracket_table: List[Dict[str, float]] = Field(default_factory=list)

    def calculate_surcharge_percent(self, current_fuel_price: float) -> float:
        """Deterministically calculate expected fuel surcharge percentage."""
        if self.schedule_type == FuelScheduleType.PERCENT:
            return self.base_surcharge_percent
        if self.schedule_type == FuelScheduleType.TABLE and self.bracket_table:
            for bracket in self.bracket_table:
                if bracket.get("min_price", 0) <= current_fuel_price < bracket.get("max_price", float("inf")):
                    return bracket.get("percent", self.base_surcharge_percent)
        if current_fuel_price > self.base_fuel_price:
            steps = (current_fuel_price - self.base_fuel_price) / self.price_increment
            return round(self.base_surcharge_percent + (steps * self.percent_per_increment), 2)
        return self.base_surcharge_percent


class AccessorialRule(BaseModel):
    code: str  # e.g. "DETENTION", "LUMPER", "LIFTGATE", "RESIDENTIAL", "LAYOVER"
    name: str
    rate: float
    unit: str = "flat"  # flat, hourly
    free_units: float = 0.0  # e.g. 2 free hours for detention
    max_charge: Optional[float] = None
    requires_receipt: bool = False
    requires_timestamp_evidence: bool = False
    requires_pre_authorization: bool = True


class ClassRateRule(BaseModel):
    freight_class: str  # "50", "70", "100", "250", etc.
    multiplier: float = 1.0
    min_density_pcf: Optional[float] = None
    reclass_requires_certified_scale: bool = True


class RateContractCreate(BaseModel):
    carrier_name: str
    customer_name: Optional[str] = None
    contract_number: str
    lane_origin_state: Optional[str] = None
    lane_origin_zip_prefix: Optional[str] = None
    lane_dest_state: Optional[str] = None
    lane_dest_zip_prefix: Optional[str] = None
    effective_start_date: Optional[datetime] = None
    effective_end_date: Optional[datetime] = None
    rate_type: RateType = RateType.FLAT
    base_rate: float = 0.0
    minimum_charge: float = 0.0
    fuel_schedule: Optional[Dict[str, Any]] = None
    accessorial_schedule: Optional[Dict[str, Any]] = None
    class_rate_rules: Optional[Dict[str, Any]] = None
    is_active: bool = True


class RateContractResponse(BaseModel):
    id: str
    organization_id: str
    carrier_name: str
    customer_name: Optional[str] = None
    contract_number: str
    lane_origin_state: Optional[str] = None
    lane_origin_zip_prefix: Optional[str] = None
    lane_dest_state: Optional[str] = None
    lane_dest_zip_prefix: Optional[str] = None
    effective_start_date: str
    effective_end_date: Optional[str] = None
    rate_type: str
    base_rate: float
    minimum_charge: float
    fuel_schedule: Optional[Dict[str, Any]] = None
    accessorial_schedule: Optional[Dict[str, Any]] = None
    class_rate_rules: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: str
    updated_at: str
