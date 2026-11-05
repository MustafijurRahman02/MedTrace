"""Request models. Strict validation: unknown fields rejected, strings trimmed and length-limited."""
import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVENT_TYPES = ("DISPATCHED", "RECEIVED_AT_DISTRIBUTION", "RECEIVED_AT_PHARMACY", "RETURNED")
BATCH_NO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,39}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class Strict(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class LoginIn(Strict):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class MedicineIn(Strict):
    generic_name: str = Field(min_length=1, max_length=80)
    brand_name: str = Field(min_length=1, max_length=80)
    manufacturer_name: str = Field(min_length=1, max_length=80)
    requires_cold_chain: bool = False
    default_min_temp: Optional[float] = Field(default=None, ge=-90, le=80)
    default_max_temp: Optional[float] = Field(default=None, ge=-90, le=80)

    @model_validator(mode="after")
    def _limits(self):
        lo, hi = self.default_min_temp, self.default_max_temp
        if (lo is None) != (hi is None):
            raise ValueError("Provide both minimum and maximum temperature, or neither")
        if lo is not None and lo >= hi:
            raise ValueError("Minimum temperature must be lower than maximum")
        if self.requires_cold_chain and lo is None:
            raise ValueError("Cold-chain medicines need a temperature range")
        return self


class BatchIn(Strict):
    medicine_id: int = Field(gt=0)
    batch_number: str
    manufacture_date: date
    expiry_date: date
    min_temp: Optional[float] = Field(default=None, ge=-90, le=80)
    max_temp: Optional[float] = Field(default=None, ge=-90, le=80)
    location_label: str = Field(default="Manufacturing site", min_length=1, max_length=80)

    @field_validator("batch_number")
    @classmethod
    def _bn(cls, v):
        if not BATCH_NO.match(v):
            raise ValueError("Batch number: 2-40 letters, digits, dot, dash or underscore")
        return v

    @model_validator(mode="after")
    def _chk(self):
        if self.expiry_date <= self.manufacture_date:
            raise ValueError("Expiry date must be after manufacture date")
        if (self.min_temp is None) != (self.max_temp is None):
            raise ValueError("Provide both batch temperature limits, or neither")
        if self.min_temp is not None and self.min_temp >= self.max_temp:
            raise ValueError("Minimum temperature must be lower than maximum")
        return self


class BatchStatusIn(Strict):
    status: Literal["ACTIVE", "QUARANTINED", "RECALLED"]


class EventIn(Strict):
    event_type: Literal[EVENT_TYPES]
    location_label: str = Field(min_length=1, max_length=80)
    event_time: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class ReadingIn(Strict):
    recorded_at: datetime
    temperature_value: float = Field(ge=-90, le=80)


class TempLogsIn(Strict):
    readings: Optional[list[ReadingIn]] = Field(default=None, max_length=5000)
    csv_text: Optional[str] = Field(default=None, max_length=1_000_000)
    filename: Optional[str] = Field(default=None, max_length=120)
    source_type: Literal["MANUAL", "SIMULATED"] = "MANUAL"
    source_event_id: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one(self):
        if bool(self.readings) == bool(self.csv_text):
            raise ValueError("Send either 'readings' or 'csv_text' (exactly one)")
        return self


class AlertPatch(Strict):
    status: Literal["UNDER_REVIEW", "RESOLVED", "ESCALATED"]
    note: Optional[str] = Field(default=None, max_length=500)


class ConcernIn(Strict):
    note: str = Field(min_length=3, max_length=500)


class UserIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "registrar", "distributor", "pharmacy", "reviewer"]

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        v = v.lower()
        if not EMAIL.match(v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("Password needs at least one letter and one digit")
        return v


class UserPatch(Strict):
    is_active: bool
