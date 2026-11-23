import csv
import io
from fastapi import File, UploadFile


class CsvImportOut(BaseModel):
    list_type: Literal["blacklist", "whitelist"]
    inserted: int
    updated: int
    skipped_empty: int
    skipped_existing: int


def _decode_csv_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="Unsupported CSV file encoding")


@app.post("/api/lists/{list_type}/import-csv", response_model=CsvImportOut)
async def import_list_csv(
    list_type: Literal["blacklist", "whitelist"],
    file: UploadFile = File(...),
    overwrite_desc: bool = Query(default=False, description="是否覆盖已存在 product_no 的 desc"),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    raw_bytes = await file.read()
    text = _decode_csv_bytes(raw_bytes)

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header is missing")

    # 标准化表头，只取 product_no 和 desc
    field_map = {name.strip().lower(): name for name in reader.fieldnames if name and name.strip()}
    if "product_no" not in field_map:
        raise HTTPException(status_code=400, detail="CSV must contain 'product_no' column")

    p_col = field_map["product_no"]
    d_col = field_map.get("desc")

    # CSV 内部按 product_no 去重
    csv_items: dict[str, str] = {}
    skipped_empty = 0
    for row in reader:
        p_no = str(row.get(p_col) or "").strip()
        if not p_no:
            skipped_empty += 1
            continue
        desc_val = str(row.get(d_col) or "").strip() if d_col else ""
        if p_no not in csv_items:
            csv_items[p_no] = desc_val
        elif overwrite_desc and desc_val:
            csv_items[p_no] = desc_val

    if not csv_items:
        return CsvImportOut(
            list_type=list_type,
            inserted=0,
            updated=0,
            skipped_empty=skipped_empty,
            skipped_existing=0,
        )

    model = get_model(list_type)
    existing_rows = db.execute(
        select(model).where(model.product_no.in_(list(csv_items.keys())))
    ).scalars().all()
    existing_map = {r.product_no: r for r in existing_rows}

    inserted = 0
    updated = 0
    skipped_existing = 0
    now = datetime.now()

    for p_no, desc_val in csv_items.items():
        if p_no in existing_map:
            if overwrite_desc:
                existing_map[p_no].desc = desc_val
                updated += 1
            else:
                skipped_existing += 1
        else:
            db.add(model(product_no=p_no, desc=desc_val, created_at=now))
            inserted += 1

    db.commit()

    return CsvImportOut(
        list_type=list_type,
        inserted=inserted,
        updated=updated,
        skipped_empty=skipped_empty,
        skipped_existing=skipped_existing,
    )
