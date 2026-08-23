"""
Backend for the Curriculum Job Fit Dashboard.
Runs JobSpy to scrape real, currently-open job postings and serves them
to index.html via a simple JSON API. Also serves index.html itself so
you only need to run one process.

Install:
    pip install python-jobspy fastapi uvicorn

Run:
    uvicorn server:app --reload --port 8000

Then open:
    http://localhost:8000
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import math
import os

try:
    from jobspy import scrape_jobs
except ImportError:
    scrape_jobs = None

app = FastAPI(title="Curriculum Job Fit Dashboard API")

# Allow the frontend (even if opened from file:// or a different port) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory cache so repeated searches for the same role don't
# re-scrape every single click (job boards will rate-limit/block that).
_cache = {}
_CACHE_TTL_SECONDS = 60 * 60 * 3  # 3 hours


def _clean_value(v):
    """Convert NaN / pandas-ish values into JSON-safe None."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    return v


@app.get("/api/jobs")
def get_jobs(
    title: str = Query("software engineer", description="Job title / role to search for"),
    location: str = Query("United States", description="Location to search in"),
    results: int = Query(15, ge=1, le=30, description="How many postings to return"),
):
    if scrape_jobs is None:
        return {
            "error": "python-jobspy is not installed. Run: pip install python-jobspy"
        }

    cache_key = f"{title.lower()}|{location.lower()}|{results}"
    import time
    now = time.time()

    cached = _cache.get(cache_key)
    if cached and (now - cached["time"]) < _CACHE_TTL_SECONDS:
        return cached["data"]

    # Pull more raw results than we need, since one company bulk-posting the
    # same title across many cities (e.g. Deloitte) can otherwise flood the
    # results with duplicates once we filter down for company variety.
    raw_wanted = max(results * 4, 40)

    try:
        df = scrape_jobs(
            site_name=["indeed", "linkedin", "zip_recruiter"],
            search_term=title,
            location=location,
            results_wanted=raw_wanted,
            hours_old=168,  # last 7 days
        )
    except Exception as e:
        return {"error": f"Scraping failed: {e}"}

    if df is None or df.empty:
        _cache[cache_key] = {"time": now, "data": []}
        return []

    # Cap how many postings any single company can contribute, so one
    # company's bulk multi-city posting spree doesn't crowd out everyone else.
    MAX_PER_COMPANY = 2
    company_counts = {}
    records = []

    for _, row in df.iterrows():
        company_raw = _clean_value(row.get("company"))
        company_key = (company_raw or "unknown").strip().lower()

        if company_counts.get(company_key, 0) >= MAX_PER_COMPANY:
            continue

        records.append({
            "title": _clean_value(row.get("title")),
            "company": company_raw,
            "location": _clean_value(row.get("location")),
            "job_url": _clean_value(row.get("job_url")),
            "date_posted": _clean_value(str(row.get("date_posted")) if row.get("date_posted") is not None else None),
            "min_amount": _clean_value(row.get("min_amount")),
            "max_amount": _clean_value(row.get("max_amount")),
            "description": _clean_value(row.get("description")),
            "site": _clean_value(row.get("site")),
        })
        company_counts[company_key] = company_counts.get(company_key, 0) + 1

        if len(records) >= results:
            break

    _cache[cache_key] = {"time": now, "data": records}
    return records


# --- Serve index.html as a static file so you only need to run this one process ---
_here = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(_here, "index.html"))