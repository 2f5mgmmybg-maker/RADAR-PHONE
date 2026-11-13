import asyncio
import csv
import io
import json
import os
import re
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# SQLite keeps the database in one file. Render mounts a persistent disk at /var/data.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./radar_phone_dev.db")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the local SQLite file in the background so the web server
    # opens its port immediately and Render's health check can reach it.
    async def initialize_database():
        try:
            await asyncio.to_thread(Base.metadata.create_all, engine)
            print("RADAR PHONE database initialized")
        except Exception as exc:
            print(f"RADAR PHONE database initialization failed: {type(exc).__name__}: {exc}")

    db_task = asyncio.create_task(initialize_database())
    yield
    if not db_task.done():
        db_task.cancel()


app = FastAPI(title="RADAR PHONE API", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
clients: set[WebSocket] = set()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_token(authorization: str | None = Header(default=None)):
    expected = os.getenv("RADAR_API_TOKEN", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected:
        raise HTTPException(status_code=503, detail="RADAR_API_TOKEN is not configured")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def find_column(headers: list[str], terms: tuple[str, ...]) -> str | None:
    normalized = {norm_header(h): h for h in headers}
    for key, original in normalized.items():
        if any(term in key for term in terms):
            return original
    return None


def looks_like_phone(value: Any) -> bool:
    d = digits(str(value or ""))
    return 6 <= len(d) <= 16


def parse_rows(filename: str, raw: bytes) -> list[dict[str, Any]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        decoded = raw.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(decoded[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        matrix = list(csv.reader(io.StringIO(decoded), dialect=dialect))
    elif suffix in {"xlsx", "xlsm"}:
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        matrix = list(workbook.active.iter_rows(values_only=True))
        workbook.close()
    else:
        raise HTTPException(status_code=415, detail="Formats acceptés : CSV ou XLSX")
    matrix = [list(row) for row in matrix if row and any(v is not None and str(v).strip() for v in row)]
    if not matrix:
        return []

    first = [str(v or "").strip() for v in matrix[0]]
    phone_header = find_column(first, ("phone", "mobile", "telephone", "tel", "gsm", "numero"))
    if phone_header:
        headers = [str(v or "").strip() or f"column_{i+1}" for i, v in enumerate(matrix[0])]
        data = matrix[1:]
    else:
        # Headerless spreadsheets are supported: inspect the first rows to
        # identify the column that most consistently contains phone numbers.
        width = max(len(r) for r in matrix)
        scores = []
        for col in range(width):
            vals = [r[col] if col < len(r) else None for r in matrix[: min(30, len(matrix))]]
            scores.append(sum(looks_like_phone(v) for v in vals))
        phone_index = max(range(width), key=lambda i: scores[i])
        if scores[phone_index] == 0:
            raise HTTPException(status_code=400, detail="Aucune colonne de numéro de téléphone détectée")
        headers = [f"column_{i+1}" for i in range(width)]
        headers[phone_index] = "phone"
        # Conventional no-header order when present: genre, nom, prénom,
        # téléphone, province, commune, date de naissance.
        aliases = {1: "last_name", 2: "first_name", 4: "province", 5: "commune", 6: "birth_date"}
        for idx, alias in aliases.items():
            if idx < width and idx != phone_index:
                headers[idx] = alias
        data = matrix
    return [dict(zip(headers, row)) for row in data if any(v is not None for v in row)]


def text_value(row: dict, key: str | None) -> str | None:
    if not key:
        return None
    value = row.get(key)
    return str(value).strip() if value is not None and str(value).strip() else None


def db_error(exc: Exception):
    print(f"RADAR PHONE database error: {type(exc).__name__}: {exc}")
    raise HTTPException(status_code=503, detail="Base de données indisponible. Vérifie DATABASE_URL sur Render.")


@app.get("/health")
def health():
    return {"status": "ok", "service": "RADAR PHONE"}


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/contacts/count", dependencies=[Depends(require_token)])
def contacts_count(db: Session = Depends(get_db)):
    try:
        return {"count": db.scalar(select(func.count()).select_from(Contact)) or 0}
    except Exception as exc:
        db_error(exc)


@app.post("/api/contacts/import", dependencies=[Depends(require_token)])
async def import_contacts(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (maximum 15 Mo)")
    rows = parse_rows(file.filename or "contacts.csv", raw)
    if not rows:
        raise HTTPException(status_code=400, detail="Le fichier ne contient aucune fiche")
    headers = list(rows[0].keys())
    phone_col = find_column(headers, ("phone", "mobile", "telephone", "tel", "gsm", "numero"))
    if not phone_col:
        raise HTTPException(status_code=400, detail="Colonne téléphone introuvable")
    first_col = find_column(headers, ("firstname", "prenom", "givenname"))
    last_col = find_column(headers, ("lastname", "nom", "surname", "familyname"))
    province_col = find_column(headers, ("province", "region", "departement"))
    commune_col = find_column(headers, ("commune", "city", "ville", "localite"))
    birth_col = find_column(headers, ("birthdate", "dateofbirth", "datenaissance", "naissance"))
    inserted = updated = skipped = 0
    try:
        for row in rows:
            phone = text_value(row, phone_col)
            normalized = digits(phone or "")
            if not 6 <= len(normalized) <= 16:
                skipped += 1
                continue
            existing = db.scalar(select(Contact).where(Contact.phone_digits == normalized).limit(1))
            known = {phone_col, first_col, last_col, province_col, commune_col, birth_col}
            extra = {str(k): str(v) for k, v in row.items() if k not in known and v is not None}
            values = dict(phone=phone, phone_digits=normalized,
                first_name=text_value(row, first_col), last_name=text_value(row, last_col),
                province=text_value(row, province_col), commune=text_value(row, commune_col),
                birth_date=text_value(row, birth_col), extra_json=json.dumps(extra, ensure_ascii=False))
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                db.add(Contact(**values))
                inserted += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        db_error(exc)
    return {"imported": inserted, "updated": updated, "skipped": skipped, "folder": "Dossier contacts"}


def match_contact(db: Session, phone: str):
    normalized = digits(phone)
    if not normalized:
        return None
    contact = db.scalar(select(Contact).where(Contact.phone_digits == normalized).limit(1))
    if not contact:
        return None
    return {
        "first_name": contact.first_name, "last_name": contact.last_name,
        "phone": contact.phone, "province": contact.province, "commune": contact.commune,
        "birth_date": contact.birth_date, "extra": json.loads(contact.extra_json or "{}")
    }


async def broadcast(payload: dict):
    dead = []
    for ws in list(clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


@app.post("/api/calls/incoming", dependencies=[Depends(require_token)])
async def incoming_call(payload: dict, db: Session = Depends(get_db)):
    phone = str(payload.get("phone", "")).strip()
    if not 6 <= len(digits(phone)) <= 16:
        raise HTTPException(status_code=400, detail="Numéro de téléphone invalide")
    try:
        event = CallEvent(phone=phone, status="incoming")
        db.add(event)
        db.commit()
        result = {"type": "incoming_call", "phone": phone, "contact": match_contact(db, phone)}
    except Exception as exc:
        db.rollback()
        db_error(exc)
    await broadcast(result)
    return result


@app.get("/api/calls/history", dependencies=[Depends(require_token)])
def calls_history(limit: int = 50, db: Session = Depends(get_db)):
    try:
        events = db.scalars(select(CallEvent).order_by(CallEvent.id.desc()).limit(max(1, min(limit, 200)))).all()
        return [{"phone": e.phone, "status": e.status, "created_at": e.created_at.isoformat()} for e in events]
    except Exception as exc:
        db_error(exc)


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
