# Employee Attendance & Analytics API

A RESTful API built with **Python, FastAPI, and MongoDB** for employee management, attendance tracking, attendance corrections, and workforce analytics.

## Features

- **Employee management:** Create employees and retrieve employee records with pagination.
- **Attendance tracking:** Punch in and punch out while applying attendance business rules.
- **Attendance queries:** Retrieve attendance records with supported filters and pagination.
- **Attendance corrections:** Update attendance records and maintain correction history.
- **Monthly analytics:** Calculate employee-level attendance, work hours, late arrivals, overtime, and leave metrics.
- **Department analytics:** Aggregate attendance metrics by department.
- **Late-arrival leaderboard:** Rank employees by accumulated late minutes.
- **Department trends:** Analyze daily attendance trends and moving averages.
- **Query explainability:** Inspect MongoDB query execution statistics using the explain endpoint.

## Technology Stack

- **Language:** Python
- **API framework:** FastAPI
- **Database:** MongoDB 7
- **Database driver:** PyMongo
- **API documentation:** OpenAPI / Swagger UI
- **Runtime:** Uvicorn

## Prerequisites

Install or prepare the following:

- Python 3.12 or a compatible version supported by the project dependencies
- Docker
- Git

## Project Structure

```text
candidate_kit/
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
```

## Setup and Installation

### 1. Clone the repository

```bash
git clone <YOUR_PUBLIC_REPOSITORY_URL>
cd candidate_kit
```

Replace the repository URL with your actual public GitHub repository URL.

### 2. Create and activate a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, activate the environment using the appropriate policy for your machine or run the environment's Python executable directly.

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Start MongoDB

Run MongoDB 7 in Docker:

```bash
docker run -d `
  --name hrone-mongodb `
  -p 27017:27017 `
  mongo:7
```

If the container already exists, start it instead:

```bash
docker start hrone-mongodb
```

Ensure MongoDB is accessible at `localhost:27017`.

### 5. Configure environment variables

**Windows PowerShell:**

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "hrone"
```

These commands configure the variables for the current PowerShell session. Set them again in a new session before starting the application.

Do not commit database credentials, secret keys, or local `.env` files to the repository.

### 6. Start the API

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API will be available at:

- Base URL: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

Use Swagger UI to explore the endpoints and execute requests.

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check API health |
| GET | `/employees` | Retrieve employees with pagination |
| POST | `/employees` | Create an employee |
| POST | `/attendance/punch-in` | Record attendance punch-in |
| POST | `/attendance/punch-out` | Record attendance punch-out |
| GET | `/attendance` | Retrieve attendance records |
| PATCH | `/attendance/{emp_code}/{date}` | Correct an attendance record |
| GET | `/analytics/employees/{emp_code}/monthly` | Retrieve monthly employee analytics |
| GET | `/analytics/departments/summary` | Retrieve department-level monthly metrics |
| GET | `/analytics/leaderboard/late` | Retrieve the late-arrival leaderboard |
| GET | `/analytics/departments/{department}/trend` | Retrieve daily department trends |
| GET | `/admin/explain/{endpoint}` | Inspect query execution statistics |

Refer to `/docs` for the exact parameters, request bodies, response schemas, and validation rules for each endpoint.

## Attendance and Analytics Rules

The API applies the assignment's attendance rules, including:

- Attendance dates are evaluated using Indian Standard Time (IST).
- Late arrivals are calculated against the configured shift start and grace period.
- Overtime is calculated after the shift end and subject to the minimum overtime threshold.
- Work hours are calculated from attendance timestamps and rounded to two decimal places.
- Weekdays are Monday through Friday; weekends are treated separately in attendance analytics.
- Monthly and department metrics account for employee joining dates.
- Attendance corrections should preserve the relevant audit history.
- Analytics calculations use MongoDB aggregation pipelines where required by the assignment.

Refer to the implementation and assignment specification for the exact calculation and rounding rules.

## Database Indexes

The application should create the indexes required by its principal query patterns during startup. These indexes support attendance lookups, employee-month analytics, department summaries, and other frequently executed queries.

See `app/main.py` for the actual index definitions.

## Testing

1. Start MongoDB.
2. Configure the environment variables.
3. Start the FastAPI application.
4. Open `http://127.0.0.1:8000/docs`.
5. Test each endpoint using valid inputs and relevant edge cases.
6. Check the explain endpoint to inspect query execution statistics.

## Design and Code Review Notes

- `REVIEW.md` documents identified implementation issues, their impact, and the corresponding fixes or remaining limitations.
- `DECISIONS.md` explains the principal database, concurrency, ranking, aggregation, and scalability decisions.

## Security and Configuration

- Database configuration is supplied through environment variables.
- Do not commit secrets, local environment files, virtual environments, or generated cache files.
- The application is intended to be run locally for assignment evaluation unless otherwise configured.

## Author

Developed as part of the HROne Software Engineer Trainee assignment.
