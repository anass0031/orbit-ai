"""Convert database / Arrow values into JSON-safe Python values."""
import math
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from uuid import UUID


def jsonable(v):
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date | dtime):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, bytes | bytearray):
        return v.hex()
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)
