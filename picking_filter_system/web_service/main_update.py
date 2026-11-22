class RawRecordOut(BaseModel):
    barcode: str
    item_index: int = 1  # 单个 barcode 响应下的第几条子记录（从 1 开始）
    status: str  # accepted | rejected | error
    reason: str
    product_no: str | None = None
    sku_no: str | None = None
    non_print_sku_active: bool = False
    extra_fields: dict[str, Any] = Field(default_factory=dict)


def extract_remote_candidates(payload: Any) -> list[dict[str, Any]]:
    """
    从远程 API 响应中提取多条候选项记录。
    具体 JSON 层级（如 data.list / data.items / rows 等）请在此按实际结构调整。
    """
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        # 常见多条列表字段名可在此兼容，例如 items / list / rows / records
        for key in ("items", "list", "rows", "records", "details"):
            if isinstance(data.get(key), list):
                raw_items = data[key]
                break
        else:
            raw_items = [data]
    else:
        raw_items = []

    excluded_keys = {"non_print_sku", "product_no", "sku_no"}
    candidates: list[dict[str, Any]] = []

    for row in raw_items:
        if not isinstance(row, dict):
            continue
        non_print_sku = row.get("non_print_sku")
        product_no = row.get("product_no")
        sku_no = row.get("sku_no")
        extra_fields = {k: v for k, v in row.items() if k not in excluded_keys}

        candidates.append({
            "non_print_sku": non_print_sku,
            "product_no": str(product_no).strip() if product_no is not None and str(product_no).strip() else None,
            "sku_no": str(sku_no).strip() if sku_no is not None and str(sku_no).strip() else None,
            "extra_fields": extra_fields,
        })

    return candidates


def build_groups(accepted_records: list[RawRecordOut], group_field: str) -> list[GroupSummaryOut]:
    grouped: dict[str, dict[str, Any]] = {}

    for rec in accepted_records:
        key_val = rec.product_no if group_field == "product_no" else rec.sku_no
        key = key_val if key_val else "(空值)"

        if key not in grouped:
            grouped[key] = {
                "group_key": key,
                "product_no": rec.product_no,
                "sku_no": rec.sku_no,
                "hit_count": 0,
                "barcodes": [],
                "extra_fields": rec.extra_fields,
            }
        grouped[key]["hit_count"] += 1
        if rec.barcode not in grouped[key]["barcodes"]:
            grouped[key]["barcodes"].append(rec.barcode)

    return [GroupSummaryOut(**v) for v in grouped.values()]
