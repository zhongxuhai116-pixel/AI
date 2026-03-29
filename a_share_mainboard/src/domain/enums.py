from enum import Enum


class BoardType(str, Enum):
    MAIN = "MAIN"


class HorizonType(int, Enum):
    H5 = 5
    H10 = 10


class SignalAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SKIP = "SKIP"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class RejectReason(str, Enum):
    NOT_MAIN_BOARD = "NOT_MAIN_BOARD"
    ST_FLAG = "ST_FLAG"
    SUSPENDED = "SUSPENDED"
    LISTING_DAYS_SHORT = "LISTING_DAYS_SHORT"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    MISSING_DATA = "MISSING_DATA"

