from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = "sqlite:///./list_filter.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class BlacklistItem(Base):
    __tablename__ = "blacklist"
    __table_args__ = (UniqueConstraint("product_no", name="uq_blacklist_product_no"),)

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    product_no = Column(String(128), nullable=False, index=True)
    desc = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class WhitelistItem(Base):
    __tablename__ = "whitelist"
    __table_args__ = (UniqueConstraint("product_no", name="uq_whitelist_product_no"),)

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    product_no = Column(String(128), nullable=False, index=True)
    desc = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


Base.metadata.create_all(bind=engine)

app = FastAPI(title="List Filter DB Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_model(list_type: Literal["blacklist", "whitelist"]):
    return BlacklistItem if list_type == "blacklist" else WhitelistItem


class ListCreateIn(BaseModel):
    product_no: str = Field(..., min_length=1, max_length=128)
    desc: str = Field(default="", max_length=255)


class ListUpdateIn(BaseModel):
    product_no: str | None = Field(default=None, min_length=1, max_length=128)
    desc: str | None = Field(default=None, max_length=255)


class ListItemOut(BaseModel):
    id: int
    product_no: str
    desc: str
    created_at: datetime

    class Config:
        from_attributes = True


class BatchCheckIn(BaseModel):
    product_nos: list[str]


class BatchCheckOut(BaseModel):
    blacklisted: list[str]
    whitelisted: list[str]


@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/lists/batch-check", response_model=BatchCheckOut)
def batch_check(payload: BatchCheckIn, db: Session = Depends(get_db)):
    print(payload)
    cleaned = list({p.strip() for p in payload.product_nos if p and p.strip()})
    if not cleaned:
        return BatchCheckOut(blacklisted=[], whitelisted=[])

    black_rows = db.execute(
        select(BlacklistItem.product_no).where(BlacklistItem.product_no.in_(cleaned))
    ).scalars().all()
    white_rows = db.execute(
        select(WhitelistItem.product_no).where(WhitelistItem.product_no.in_(cleaned))
    ).scalars().all()

    return BatchCheckOut(
        blacklisted=sorted(set(black_rows)),
        whitelisted=sorted(set(white_rows)),
    )

@app.get("/api/lists/{list_type}", response_model=list[ListItemOut])
def list_items(
    list_type: Literal["blacklist", "whitelist"],
    q: str | None = Query(default=None, description="按 product_no 或 desc 模糊搜索"),
    db: Session = Depends(get_db),
):
    model = get_model(list_type)
    stmt = select(model).order_by(model.id.desc())
    if q and q.strip():
        kw = f"%{q.strip()}%"
        stmt = stmt.where((model.product_no.like(kw)) | (model.desc.like(kw)))
    rows = db.execute(stmt).scalars().all()
    return rows


@app.post("/api/lists/{list_type}", response_model=ListItemOut, status_code=201)
def create_item(
    list_type: Literal["blacklist", "whitelist"],
    payload: ListCreateIn,
    db: Session = Depends(get_db),
):
    model = get_model(list_type)
    p_no = payload.product_no.strip()
    existing = db.execute(select(model).where(model.product_no == p_no)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"{p_no} already exists in {list_type}")

    row = model(
        product_no=p_no,
        desc=payload.desc.strip(),
        created_at=datetime.now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@app.put("/api/lists/{list_type}/{item_id}", response_model=ListItemOut)
def update_item(
    list_type: Literal["blacklist", "whitelist"],
    item_id: int,
    payload: ListUpdateIn,
    db: Session = Depends(get_db),
):
    model = get_model(list_type)
    row = db.get(model, item_id)
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")

    if payload.product_no is not None:
        new_p_no = payload.product_no.strip()
        dup = db.execute(
            select(model).where(model.product_no == new_p_no, model.id != item_id)
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail=f"{new_p_no} already exists in {list_type}")
        row.product_no = new_p_no

    if payload.desc is not None:
        row.desc = payload.desc.strip()

    db.commit()
    db.refresh(row)
    return row


@app.delete("/api/lists/{list_type}/{item_id}")
def delete_item(
    list_type: Literal["blacklist", "whitelist"],
    item_id: int,
    db: Session = Depends(get_db),
):
    model = get_model(list_type)
    row = db.get(model, item_id)
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": item_id}





if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
