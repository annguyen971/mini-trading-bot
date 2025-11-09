from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TAData(BaseModel):
    """Standardized internal representation for Technical Analysis data."""
    model_config = ConfigDict(strict=True)

    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float = Field(..., alias='value') # Example of alias for different source field names


class SAData(BaseModel):
    """Standardized internal representation for Social/News Analysis data."""
    model_config = ConfigDict(strict=True)

    id: str
    source: str
    author: Optional[str] = None
    url: str
    text: str
    publisher_time: datetime
    first_seen_time: datetime
    entities: Optional[List[str]] = []


class MacroData(BaseModel):
    """Standardized internal representation for Macroeconomic data."""
    model_config = ConfigDict(strict=True)

    effective_date: date
    policy_rate: Optional[float] = None
    usdvnd_rate: Optional[float] = None
    cpi_yoy: Optional[float] = None


def adapt_ta_data(raw_data: dict) -> TAData:
    """Placeholder function to adapt raw TA data to the standard model."""
    return TAData.model_validate(raw_data)

def adapt_sa_data(raw_data: dict) -> SAData:
    """Placeholder function to adapt raw SA data to the standard model."""
    return SAData.model_validate(raw_data)

def adapt_macro_data(raw_data: dict) -> MacroData:
    """Placeholder function to adapt raw Macro data to the standard model."""
    return MacroData.model_validate(raw_data)
