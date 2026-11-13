import csv
import io
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./radar_phone_dev.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(40), index=True)
    phone_digits: Mapped[str] = mapped_column(String(40), index=True)
    first_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    province: Mapped[str | None] = mapped_column(String(160), nullable=True)
    commune: Mapped[str | None] = mapped_column(String(160), nullable=True)
    birth_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    extra_json: Mapped[str] = mapped_column(Text, default="{}")


class CallEvent(Base):
    __tablename__ = "call_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="incoming")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# Database initialization is deliberately non-fatal: keep the web server available
# even if PostgreSQL is temporarily unavailable during startup.
@app.on_event("startup")
def initialize_database():
    try:
        Base.metadata.create_all(engine)
        print("RADAR PHONE database initialized")
    except Exception as exc:
        print(f"RADAR PHONE database initialization failed: {exc}")

app = FastAPI(title="RADAR PHONE API", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
clients: set[WebSocket] = set()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_token(authorization: str | None = Header(default=None)):
    expected = os.getenv("RADAR_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="RADAR_API_TOKEN is not configured")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not supplied or supplied != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def find_column(headers: list[str], terms: tuple[str, ...]) -> str | None:
    normalized = {re.sub(r"[^a-z0-9]", "", h.lower()): h for h in headers}
    for key, original in normalized.items():
        if any(term in key for term in terms):
            return original
    return None


def parse_rows(filename: str, raw: bytes) -> list[dict[str, Any]]:
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "csv":
        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if suffix in {"xlsx", "xlsm"}:
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(v or "").strip() for v in rows[0]]
        return [dict(zip(headers, row)) for row in rows[1:] if any(v is not None for v in row)]
    raise HTTPException(status_code=415, detail="Import CSV or XLSX only")


def text_value(row: dict, key: str | None) -> str | None:
    if not key:
        return None
    value = row.get(key)
    return str(value).strip() if value is not None and str(value).strip() else None


@app.get("/health")
def health():
    return {"status": "ok", "service": "RADAR PHONE"}


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/api/contacts/count", dependencies=[Depends(require_token)])
def contacts_count(db: Session = Depends(get_db)):
    from sqlalchemy import func
    return {"count": db.scalar(select(func.count()).select_from(Contact)) or 0}


@app.post("/api/contacts/import", dependencies=[Depends(require_token)])
async def import_contacts(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    rows = parse_rows(file.filename or "contacts.csv", raw)
    if not rows:
        raise HTTPException(status_code=400, detail="No contact rows found")
    headers = list(rows[0].keys())
    phone_col = find_column(headers, ("phone", "mobile", "telephone", "tel", "gsm", "numero"))
    if not phone_col:
        raise HTTPException(status_code=400, detail="Phone column not detected. Add a column named phone, mobile, telephone or numero.")
    first_col = find_column(headers, ("firstname", "prenom", "givenname"))
    last_col = find_column(headers, ("lastname", "nom", "surname", "familyname"))
    province_col = find_column(headers, ("province", "region", "departement"))
    commune_col = find_column(headers, ("commune", "city", "ville", "localite"))
    birth_col = find_column(headers, ("birthdate", "dateofbirth", "datenaissance", "naissance"))
    inserted = 0
    for row in rows:
        phone = text_value(row, phone_col)
        normalized = digits(phone or "")
        if len(normalized) < 6:
            continue
        known = {phone_col, first_col, last_col, province_col, commune_col, birth_col}
        extra = {str(k): str(v) for k, v in row.items() if k not in known and v is not None}
        db.add(Contact(
            phone=phone, phone_digits=normalized,
            first_name=text_value(row, first_col), last_name=text_value(row, last_col),
            province=text_value(row, province_col), commune=text_value(row, commune_col),
            birth_date=text_value(row, birth_col), extra_json=json.dumps(extra, ensure_ascii=False)
        ))
        inserted += 1
    db.commit()
    return {"imported": inserted, "folder": "Dossier contacts"}


def match_contact(db: Session, phone: str):
    normalized = digits(phone)
    if not normalized:
        return None
    candidates = db.scalars(select(Contact).where(Contact.phone_digits == normalized).limit(10)).all()
    if not candidates and len(normalized) >= 8:
        candidates = db.scalars(select(Contact).where(Contact.phone_digits.endswith(normalized[-8:])).limit(10)).all()
    if not candidates:
        return None
    c = candidates[0]
    return {
        "first_name": c.first_name, "last_name": c.last_name,
        "phone": c.phone, "province": c.province, "commune": c.commune,
        "birth_date": c.birth_date, "extra": json.loads(c.extra_json or "{}")
    }


async def broadcast(payload: dict):
    dead = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


@app.post("/api/calls/incoming", dependencies=[Depends(require_token)])
async def incoming_call(payload: dict, db: Session = Depends(get_db)):
    phone = str(payload.get("phone", "")).strip()
    if not digits(phone):
        raise HTTPException(status_code=400, detail="A valid caller phone number is required")
    event = CallEvent(phone=phone, status="incoming")
    db.add(event)
    db.commit()
    result = {"type": "incoming_call", "phone": phone, "contact": match_contact(db, phone)}
    await broadcast(result)
    return result


@app.websocket("/ws/calls")
async def calls_socket(websocket: WebSocket):
    expected = os.getenv("RADAR_API_TOKEN", "")
    offered = websocket.scope.get("subprotocols", [])
    supplied = offered[1] if len(offered) > 1 and offered[0] == "radar-phone" else ""
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol="radar-phone")
    clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
