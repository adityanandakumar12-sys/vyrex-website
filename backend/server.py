"""
AEVRIX Backend — Lead Management API
Run: python server.py
Admin: http://localhost:8001/admin  (user: aevrix / pass: sovereign2024)
"""

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, validator
from typing import Optional
from pathlib import Path
from datetime import datetime
import sqlite3, secrets, os, csv, io

# ── CONFIG ────────────────────────────────────────────────────────────────
ADMIN_USER = os.getenv("AEVRIX_ADMIN_USER", "aevrix")
ADMIN_PASS = os.getenv("AEVRIX_ADMIN_PASS", "sovereign2024")
DB_PATH = Path(__file__).parent / "leads.db"
ADMIN_HTML = Path(__file__).parent / "admin.html"

# ── APP ───────────────────────────────────────────────────────────────────
app = FastAPI(title="AEVRIX Lead Management", version="1.0.0", docs_url=None, redoc_url=None)

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

security = HTTPBasic()

# ── DATABASE ──────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                email       TEXT    NOT NULL,
                company     TEXT    DEFAULT '',
                service     TEXT    DEFAULT '',
                budget      TEXT    DEFAULT '',
                message     TEXT    DEFAULT '',
                status      TEXT    DEFAULT 'new',
                notes       TEXT    DEFAULT '',
                ip          TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now')),
                updated_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

init_db()

# ── AUTH ──────────────────────────────────────────────────────────────────
def require_admin(creds: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(creds.username.encode(), ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(creds.password.encode(), ADMIN_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username

# ── MODELS ────────────────────────────────────────────────────────────────
class ContactForm(BaseModel):
    name: str
    email: str
    company: str = ""
    service: str = ""
    budget: str = ""
    message: str

    @validator('name')
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('Name cannot be empty')
        return v

    @validator('email')
    def email_valid(cls, v):
        v = v.strip().lower()
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email address')
        return v

    @validator('message')
    def message_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('Message cannot be empty')
        return v

class LeadUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None

# ── ROUTES ────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return {"service": "AEVRIX Backend", "version": "1.0.0", "status": "operational"}

@app.post("/api/contact")
async def submit_contact(form: ContactForm, request: Request):
    ip = request.client.host if request.client else ""
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO leads (name,email,company,service,budget,message,ip) VALUES (?,?,?,?,?,?,?)",
                (form.name, form.email, form.company, form.service, form.budget, form.message, ip)
            )
            lead_id = cur.lastrowid
            conn.commit()
        return {
            "success": True,
            "id": lead_id,
            "message": "Thank you. We'll be in touch within 24 hours."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads")
def get_leads(
    filter_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    admin: str = Depends(require_admin)
):
    with get_db() as conn:
        if filter_status:
            rows = conn.execute(
                "SELECT * FROM leads WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (filter_status, limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/stats")
def get_stats(admin: str = Depends(require_admin)):
    with get_db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        new    = conn.execute("SELECT COUNT(*) FROM leads WHERE status='new'").fetchone()[0]
        today  = conn.execute("SELECT COUNT(*) FROM leads WHERE DATE(created_at)=DATE('now')").fetchone()[0]
        won    = conn.execute("SELECT COUNT(*) FROM leads WHERE status='won'").fetchone()[0]
        qual   = conn.execute("SELECT COUNT(*) FROM leads WHERE status='qualified'").fetchone()[0]
        by_svc = conn.execute("SELECT service, COUNT(*) FROM leads GROUP BY service").fetchall()
    return {
        "total": total,
        "new": new,
        "today": today,
        "won": won,
        "qualified": qual,
        "conversion_rate": round((won / total * 100) if total > 0 else 0, 1),
        "by_service": {r[0] or "Unknown": r[1] for r in by_svc},
    }

@app.put("/api/leads/{lead_id}")
def update_lead(lead_id: int, update: LeadUpdate, admin: str = Depends(require_admin)):
    cols, vals = [], []
    if update.status is not None:
        valid = {'new','contacted','qualified','won','lost'}
        if update.status not in valid:
            raise HTTPException(400, f"status must be one of: {', '.join(valid)}")
        cols.append("status=?"); vals.append(update.status)
    if update.notes is not None:
        cols.append("notes=?"); vals.append(update.notes)
    if not cols:
        raise HTTPException(400, "Nothing to update")
    cols.append("updated_at=?"); vals.append(datetime.now().isoformat())
    vals.append(lead_id)
    with get_db() as conn:
        conn.execute(f"UPDATE leads SET {','.join(cols)} WHERE id=?", vals)
        conn.commit()
    return {"success": True}

@app.delete("/api/leads/{lead_id}")
def delete_lead(lead_id: int, admin: str = Depends(require_admin)):
    with get_db() as conn:
        conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        conn.commit()
    return {"success": True}

@app.get("/api/export")
def export_csv(admin: str = Depends(require_admin)):
    from fastapi.responses import StreamingResponse
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Name','Email','Company','Service','Budget','Message','Status','Notes','IP','Created','Updated'])
    for r in rows:
        writer.writerow([r['id'],r['name'],r['email'],r['company'],r['service'],r['budget'],
                         r['message'],r['status'],r['notes'],r['ip'],r['created_at'],r['updated_at']])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=aevrix_leads_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@app.get("/admin", response_class=HTMLResponse)
def admin_page(admin: str = Depends(require_admin)):
    if ADMIN_HTML.exists():
        return ADMIN_HTML.read_text(encoding='utf-8')
    return "<h1>admin.html not found</h1>"

# ── RUN ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("\n  AEVRIX Backend starting...")
    print("  API:   http://localhost:8001")
    print("  Admin: http://localhost:8001/admin")
    print("  Creds: aevrix / sovereign2024\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8001, reload=True)
