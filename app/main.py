"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
from calendar  import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, StrictInt, Field, field_validator, model_validator
from pymongo import MongoClient

from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    # One employee code per employee
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employee_code",
    )

    # One attendance record per employee per attendance date
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_employee_date",
    )

    # Support attendance filtering, sorting and pagination
    
# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def attendance_date_and_shift_start(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> tuple[str, datetime]:
    local_in = punch_in.astimezone(IST)
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    is_overnight = (end_hour, end_minute) <= (
        start_hour, start_minute
    )

    attendance_day = local_in.date()

    if is_overnight and (
        local_in.hour, local_in.minute
    ) < (end_hour, end_minute):
        attendance_day -= timedelta(days=1)

    shift_start_local = datetime(
        attendance_day.year,
        attendance_day.month,
        attendance_day.day,
        start_hour,
        start_minute,
        tzinfo=IST,
    )

    return attendance_day.isoformat(), shift_start_local


def round_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(hours)


def compute_overtime_minutes(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    attendance_date: str,
) -> int:
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    start = datetime.fromisoformat(attendance_date).replace(
        hour=start_hour,
        minute=start_minute,
        tzinfo=IST,
    )
    end = datetime.fromisoformat(attendance_date).replace(
        hour=end_hour,
        minute=end_minute,
        tzinfo=IST,
    )

    # Overnight shift: the shift ends on the following calendar day.
    if (end_hour, end_minute) <= (start_hour, start_minute):
        end += timedelta(days=1)

    elapsed_seconds = int(
        (punch_out.astimezone(IST) - end).total_seconds()
    )
    whole_minutes = elapsed_seconds // 60

    return whole_minutes if whole_minutes >= 30 else 0


def compute_late_minutes(
    punch_in: datetime,
    shift_start_local: datetime,
) -> int:
    punch_in_local = punch_in.astimezone(IST)

    elapsed_seconds = int(
        (punch_in_local - shift_start_local).total_seconds()
    )

    # No lateness during the first 10 minutes.
    if elapsed_seconds <= 10 * 60:
        return 0

    return elapsed_seconds // 60

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(
        pattern=r"^EMP\d{4,6}$"
    )
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self



class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None


class RegularizeIn(BaseModel):
    status: Optional[Literal[
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()

    # The server, not the client, sets creation time.
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    # Convert the stored UTC datetime to epoch milliseconds.
    doc["created_at"] = int(
        doc["created_at"].timestamp() * 1000
    )

    return doc




@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination parameters",
        )

    query = {}

    if department is not None:
        query["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for employee in items:
        created_at = employee.get("created_at")

        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(
                    tzinfo=timezone.utc
                )

            employee["created_at"] = int(
                created_at.timestamp() * 1000
            )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0},
    )

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Convert the supplied epoch milliseconds to a whole-second UTC instant.
    if body.punched_at is None:
        punched_at = datetime.now(timezone.utc).replace(
            microsecond=0
        )
    else:
        punched_at = datetime.fromtimestamp(
            body.punched_at // 1000,
            tz=timezone.utc,
        )

    attendance_date, shift_start_local = (
        attendance_date_and_shift_start(
            punched_at,
            emp["shift_start"],
            emp["shift_end"],
        )
    )

    late_minutes = compute_late_minutes(
        punched_at.astimezone(IST),
        shift_start_local,
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    # MongoDB stores BSON dates; the API returns epoch milliseconds.
    doc["punch_in"] = int(punched_at.timestamp() * 1000)

    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # 1. Verify the employee exists.
    emp = db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0},
    )

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # 2. Get punch-out time, truncate to whole seconds (R1).
    if body.punched_at is None:
        punched_at = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        punched_at = datetime.fromtimestamp(
            body.punched_at // 1000,
            tz=timezone.utc,
        )

    # 3. Find the most recent eligible record, including overnight shifts.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": punched_at},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No eligible punch-in found",
        )

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    elapsed = (punched_at - punch_in_time).total_seconds()

    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    # 4. Calculate the derived fields.
    work_hours = round_work_hours(punch_in_time, punched_at)

    overtime_minutes = compute_overtime_minutes(
        punched_at,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )

    half_day = work_hours < 4.50

    # 5. Atomic update: only one concurrent request can close the record.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    # 6. Return the API representation without MongoDB's internal _id.
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    return updated


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    # Validate the path date.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
        if parsed_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date")

    # Verify employee and attendance record.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    supplied = body.model_fields_set

    # Explicit null timestamps are not valid epoch-millisecond values.
    for field in ("punch_in", "punch_out"):
        if field in supplied and getattr(body, field) is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} must be an epoch-milliseconds integer",
            )

    # Convert stored naive BSON datetimes to aware UTC datetimes.
    def as_utc(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    old_status = record.get("status")
    old_in = as_utc(record.get("punch_in"))
    old_out = as_utc(record.get("punch_out"))

    new_status = body.status if "status" in supplied else old_status

    def from_epoch_ms(value):
        # R1: truncate to whole seconds before calculations/storage.
        return datetime.fromtimestamp(
            value // 1000,
            tz=timezone.utc,
        )

    new_in = (
        from_epoch_ms(body.punch_in)
        if "punch_in" in supplied
        else old_in
    )
    new_out = (
        from_epoch_ms(body.punch_out)
        if "punch_out" in supplied
        else old_out
    )

    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(
                status_code=422,
                detail="ABSENT/LEAVE cannot be supplied with punch times",
            )
        new_in = None
        new_out = None
    elif new_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in",
        )

    # Punch-in must belong to the record's IST attendance date.
    if new_in is not None:
        calculated_date, _ = attendance_date_and_shift_start(
            new_in,
            emp["shift_start"],
            emp["shift_end"],
        )
        if calculated_date != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

    # Validate punch-out relative to the final punch-in.
    if new_out is not None:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )
        seconds = (new_out - new_in).total_seconds()
        if seconds <= 0 or seconds > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    # Build only actual changes for the audit trail.
    changes = {}

    def epoch_ms(value):
        return int(value.timestamp() * 1000) if value is not None else None

    if new_status != old_status:
        changes["status"] = {"before": old_status, "after": new_status}
    if new_in != old_in:
        changes["punch_in"] = {
            "before": epoch_ms(old_in),
            "after": epoch_ms(new_in),
        }
    if new_out != old_out:
        changes["punch_out"] = {
            "before": epoch_ms(old_out),
            "after": epoch_ms(new_out),
        }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Request does not change the record",
        )

    # Recompute all derived values from the final state.
    if new_in is None:
        late_minutes = 0
    else:
        _, shift_start_local = attendance_date_and_shift_start(
            new_in,
            emp["shift_start"],
            emp["shift_end"],
        )
        late_minutes = compute_late_minutes(
            new_in.astimezone(IST),
            shift_start_local,
        )

    if new_in is not None and new_out is not None:
        work_hours = round_work_hours(new_in, new_out)
        overtime_minutes = compute_overtime_minutes(
            new_out,
            emp["shift_start"],
            emp["shift_end"],
            date,
        )
        half_day = work_hours < 4.50
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    updates = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    # Optimistic concurrency: only update if history is unchanged.
    history = record.get("history", [])
    history_filter = (
        {"history": {"$exists": False}}
        if "history" not in record
        else {"history": history}
    )

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], **history_filter},
        {
            "$set": updates,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Record changed concurrently; retry the correction",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    # Convert all API instants to epoch milliseconds.
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            updated[field] = epoch_ms(as_utc(value))

    for entry in updated.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = epoch_ms(as_utc(entry["at"]))

    return updated



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate YYYY-MM.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    year, month_number = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_number)[1]

    month_start = f"{month}-01"
    month_end = f"{month}-{last_day:02d}"

    # Working days start from the later of month-start and joined_on.
    joined_on = employee["joined_on"]
    first_date = max(
        date(year, month_number, 1),
        date.fromisoformat(joined_on),
    )

    working_days = 0
    current = first_date
    final_date = date(year, month_number, last_day)

    while current <= final_date:
        if current.weekday() < 5:
            working_days += 1
        current += timedelta(days=1)

    # Aggregate attendance in MongoDB; don't fetch all logs into Python.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lte": month_end},
            }
        },
        {
            "$project": {
                "date": 1,
                "status": 1,
                "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                "half_day": {"$ifNull": ["$half_day", False]},
                "weekday": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d",
                            "timezone": "Asia/Kolkata",
                        }
                    }
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": ["$weekday", 2]},
                                    {"$lte": ["$weekday", 6]},
                                ]
                            },
                            {
                                "$cond": [
                                    "$half_day",
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    stats = results[0] if results else {}

    present_days = stats.get("present_days", 0)

    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = float(
            (
                Decimal(str(present_days))
                / Decimal(working_days)
                * Decimal("100")
            ).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: str | None = None,
):
    year, month_num = map(int, month.split("-"))

    start_date = date(year, month_num, 1)
    if month_num == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month_num + 1, 1)

    last_day = (end_date - date.resolution).isoformat()
    first_day = start_date.isoformat()

    employee_match = {
        "joined_on": {"$lte": last_day}
    }
    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", first_day]},
                                    {"$lte": ["$date", last_day]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$logs.date",
                                                        "format": "%Y-%m-%d",
                                                    }
                                                }
                                            },
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_values": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]},
                                ]
                            },
                            "$logs.work_hours",
                            None,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$headcount_codes"},
                "valid_hours": {
                    "$filter": {
                        "input": "$work_hours_values",
                        "as": "hours",
                        "cond": {"$ne": ["$$hours", None]},
                    }
                },
            }
        },
        {
            "$set": {
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": [{"$size": "$valid_hours"}, 0]},
                        {"$avg": "$valid_hours"},
                        None,
                    ]
                }
            }
        },
        {"$match": {"headcount": {"$gt": 0}}},
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    results = list(db.employees.aggregate(pipeline))

    # Round averages to 2 decimal places using half-up rounding.
    for item in results:
        value = item["avg_work_hours"]
        if value is not None:
            item["avg_work_hours"] = float(
                Decimal(str(value)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )

    return results

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: str | None = None,
):
    year, month_num = map(int, month.split("-"))

    start_date = f"{year:04d}-{month_num:02d}-01"

    if month_num == 12:
        next_month = f"{year + 1:04d}-01-01"
    else:
        next_month = f"{year:04d}-{month_num + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lt": next_month},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {
            "$match": {"rank": {"$lte": limit}}
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    # Validate the requested range.
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    first_day = from_date.isoformat()
    last_day = to_date.isoformat()

    # Verify that the department exists.
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    pipeline = [
        {
            "$match": {
                "date": {"$gte": first_day, "$lte": last_day},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateFromString": {
                        "dateString": "$_id",
                        "format": "%Y-%m-%d",
                    }
                },
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": "full",
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                    }
                },
                "weekday": {"$dayOfWeek": "$date"},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$weekday", 2]},
                        {"$lte": ["$weekday", 6]},
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    # Round rates to four decimal places using half-up rounding.
    for item in items:
        for field in ("attendance_rate", "moving_avg_7d"):
            value = item[field]
            if value is not None:
                item[field] = float(
                    Decimal(str(value)).quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP,
                    )
                )

    return {
        "department": department,
        "items": items,
    }
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: str | None = None,
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(422, detail="Unsupported endpoint")

    if endpoint in {
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
    } and not month:
        raise HTTPException(422, detail="month is required")

    if endpoint == "employee_monthly" and not emp_code:
        raise HTTPException(422, detail="emp_code is required")

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code
        if date_from:
            query["date"] = {"$gte": date_from.isoformat()}
        if date_to:
            query.setdefault("date", {})["$lte"] = date_to.isoformat()
        if status:
            query["status"] = status

        explain = db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    elif endpoint == "employee_monthly":
        year, month_num = map(int, month.split("-"))
        first = f"{year:04d}-{month_num:02d}-01"
        last = f"{year:04d}-{month_num:02d}-{monthrange(year, month_num)[1]:02d}"

        pipeline = [
            {"$match": {
                "emp_code": emp_code,
                "date": {"$gte": first, "$lte": last},
            }},
        ]

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    elif endpoint == "department_summary":
        year, month_num = map(int, month.split("-"))
        first = f"{year:04d}-{month_num:02d}-01"
        last = f"{year:04d}-{month_num:02d}-{monthrange(year, month_num)[1]:02d}"

        employee_filter = {"joined_on": {"$lte": last}}
        if department is not None:
            employee_filter["department"] = department

        explain = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": [
                    {"$match": employee_filter},
                    {"$lookup": {
                        "from": "attendance_logs",
                        "let": {"code": "$emp_code"},
                        "pipeline": [
                            {"$match": {"$expr": {"$and": [
                                {"$eq": ["$emp_code", "$$code"]},
                                {"$gte": ["$date", first]},
                                {"$lte": ["$date", last]},
                            ]}}},
                        ],
                        "as": "logs",
                    }},
                ],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "employees"

    elif endpoint == "late_leaderboard":
        year, month_num = map(int, month.split("-"))
        first = f"{year:04d}-{month_num:02d}-01"
        next_month = (
            f"{year + 1:04d}-01-01"
            if month_num == 12
            else f"{year:04d}-{month_num + 1:02d}-01"
        )

        pipeline = [
            {"$match": {
                "date": {"$gte": first, "$lt": next_month},
                "late_minutes": {"$gt": 0},
            }},
            {"$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }},
        ]

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    else:
        if not department or not from_date or not to_date:
            raise HTTPException(
                422,
                detail="department, from and to are required",
            )
        if to_date < from_date or (to_date - from_date).days + 1 > 92:
            raise HTTPException(422, detail="Invalid date range")

        pipeline = [
            {"$match": {
                "date": {
                    "$gte": from_date.isoformat(),
                    "$lte": to_date.isoformat(),
                },
            }},
            {"$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }},
            {"$unwind": "$employee"},
            {"$match": {"employee.department": department}},
        ]

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": explain,
    }

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
