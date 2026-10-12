# Design Decisions — Employee Attendance & Analytics API

## 1. Indexing Strategy

MongoDB indexes should support the API's primary access patterns. Employee codes should be indexed for employee lookups. Attendance records should have indexes aligned with employee/date queries, date-range filtering, and analytics pipelines. A unique compound index on employee code and attendance date should enforce one attendance record per employee per day, if consistent with the assignment's data model. Index selection should be validated using `explain("executionStats")` rather than assumed to improve performance.

## 2. Concurrent Identical Punch-In Requests

Application-level existence checks alone are insufficient because two concurrent requests can both observe that no record exists. A database-enforced unique constraint on the employee/date key is the preferred protection against duplicate records. The API should handle duplicate-key errors and return the response required by the API contract. The final behavior must be verified against the actual index and error-handling implementation.

## 3. Leaderboard Ties at the Cutoff

Competition ranking should be used for equal total late minutes. Employees with equal totals receive the same rank, and the next rank is skipped accordingly. Ranking must occur before applying the requested rank limit so that employees tied at the cutoff are handled consistently. Employee code provides deterministic ordering among equal totals.

## 4. Employees Without Attendance Logs

Department headcount should be derived from eligible employee records, not only attendance records. Employees whose joining dates are on or before the reporting period's end should contribute to headcount even if they have no attendance logs. Attendance metrics should use only records qualifying under their respective definitions.

## 5. Scaling to 100 Times the Data

For larger datasets, I would validate index selectivity and execution plans, reduce unnecessary document processing, optimize aggregation pipelines, and monitor query latency and resource utilization. I would also evaluate pagination costs, connection-pool configuration, workload patterns, and aggregation memory usage. Any architectural changes should be driven by measured bottlenecks rather than assumptions.
