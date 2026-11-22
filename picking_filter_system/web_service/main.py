import asyncio
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ================= 配置区（按实际环境修改） =================
REMOTE_API_URL = os.getenv("REMOTE_API_URL", "https://mps.casetify.com/api/shipping_task")
DB_SERVICE_URL = os.getenv("DB_SERVICE_URL", "http://192.168.201.251:8001")
INITIAL_TOKEN = os.getenv("REMOTE_API_TOKEN", "")
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "10"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10.0"))
# ==========================================================

app = FastAPI(title="Product Filter Web Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TokenState:
    def __init__(self, token: str = ""):
        self.token: str = token


token_state = TokenState(INITIAL_TOKEN)


class TokenUpdateIn(BaseModel):
    token: str = Field(..., min_length=1)


class ScanFilterIn(BaseModel):
    barcodes: list[str] = Field(default_factory=list)


class RawRecordOut(BaseModel):
    barcode: str
    status: str  # accepted | rejected | error
    reason: str
    product_no: str | None = None
    sku_no: str | None = None
    non_print_sku_active: bool = False
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class GroupSummaryOut(BaseModel):
    group_key: str
    product_no: str | None = None
    sku_no: str | None = None
    hit_count: int
    barcodes: list[str]
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class ScanFilterOut(BaseModel):
    total_input: int
    unique_barcodes: int
    accepted_count: int
    rejected_count: int
    error_count: int
    records: list[RawRecordOut]
    by_product_no: list[GroupSummaryOut]
    by_sku_no: list[GroupSummaryOut]


def extract_remote_fields(payload: Any) -> dict[str, Any]:
    """
    从远程 API 响应中提取需要的字段。
    层级结构较多时，请在此函数中按实际返回 JSON 调整取值路径。
    """
    if not isinstance(payload, dict):
        return {
            "non_print_sku": None,
            "product_no": None,
            "sku_no": None,
            "extra_fields": {},
        }

    # 若响应外层包了 data，可在此兼容，例如：
    # data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    non_print_sku = data.get("non_print_sku")
    product_no = data.get("product_no")
    sku_no = data.get("sku_no")

    # 保留除核心控制字段外的其它字段，便于前端展示或扩展
    excluded_keys = {"non_print_sku", "product_no", "sku_no"}
    extra_fields = {k: v for k, v in data.items() if k not in excluded_keys}

    return {
        "non_print_sku": non_print_sku,
        "product_no": str(product_no).strip() if product_no is not None and str(product_no).strip() else None,
        "sku_no": str(sku_no).strip() if sku_no is not None and str(sku_no).strip() else None,
        "extra_fields": extra_fields,
    }


def is_non_print_sku_true(val: Any) -> bool:
    """
    按约定：non_print_sku 为真时是一个字典结构，为假时是 null。
    """
    return isinstance(val, dict) and len(val) > 0


async def fetch_single_barcode(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    barcode: str,
    auth_token: str,
) -> dict[str, Any]:
    headers = {"Authorization": auth_token}
    params = {
        "search_for_bucket": barcode,
        "sort_slot": "true",
    }
    async with semaphore:
        try:
            resp = await client.get(REMOTE_API_URL, params=params, headers=headers)
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


async def query_db_lists(product_nos: list[str]) -> tuple[set[str], set[str]]:
    if not product_nos:
        return set(), set()
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{DB_SERVICE_URL.rstrip('/')}/api/lists/batch-check",
            json={"product_nos": product_nos},
        )
        resp.raise_for_status()
        body = resp.json()
        return set(body.get("blacklisted", [])), set(body.get("whitelisted", []))


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
        grouped[key]["barcodes"].append(rec.barcode)

    return [GroupSummaryOut(**v) for v in grouped.values()]


@app.get("/health")
def health():
    return {
        "status": "ok",
        "token_configured": bool(token_state.token.strip()),
        "db_service_url": DB_SERVICE_URL,
        "remote_api_url": REMOTE_API_URL,
    }


@app.get("/api/token/status")
def get_token_status():
    t = token_state.token.strip()
    masked = f"{t[:6]}...{t[-4:]}" if len(t) > 12 else ("已设置" if t else "未设置")
    return {"configured": bool(t), "masked_token": masked}


@app.post("/api/token")
def update_token(payload: TokenUpdateIn):
    token_state.token = payload.token.strip()
    return {"ok": True, "message": "Token updated in memory"}


@app.post("/api/filter", response_model=ScanFilterOut)
async def run_filter(payload: ScanFilterIn):
    current_token = token_state.token.strip()
    if not current_token:
        raise HTTPException(status_code=400, detail="Remote API token is not set in memory")

    raw_inputs = [b.strip() for b in payload.barcodes if b and b.strip()]
    unique_barcodes = list(dict.fromkeys(raw_inputs))

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            fetch_single_barcode(client, semaphore, bc, current_token)
            for bc in unique_barcodes
        ]
        api_results = await asyncio.gather(*tasks)

    parsed_items: list[dict[str, Any]] = []
    candidate_product_nos: list[str] = []

    for bc, api_res in zip(unique_barcodes, api_results):
        if not api_res["ok"]:
            parsed_items.append({
                "barcode": bc,
                "api_ok": False,
                "api_error": api_res["error"],
            })
            continue

        extracted = extract_remote_fields(api_res["data"])
        non_print_active = is_non_print_sku_true(extracted["non_print_sku"])
        p_no = extracted["product_no"]

        if non_print_active and p_no:
            candidate_product_nos.append(p_no)

        parsed_items.append({
            "barcode": bc,
            "api_ok": True,
            "non_print_sku_active": non_print_active,
            "product_no": p_no,
            "sku_no": extracted["sku_no"],
            "extra_fields": extracted["extra_fields"],
        })

    try:
        blacklisted_set, whitelisted_set = await query_db_lists(candidate_product_nos)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to query DB service: {exc}")

    records: list[RawRecordOut] = []
    accepted_records: list[RawRecordOut] = []
    rejected_count = 0
    error_count = 0

    for item in parsed_items:
        bc = item["barcode"]
        if not item["api_ok"]:
            error_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                status="error",
                reason=f"远程API请求失败: {item['api_error']}",
            ))
            continue

        if not item["non_print_sku_active"]:
            rejected_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                status="rejected",
                reason="non_print_sku 为空或非真",
                product_no=item["product_no"],
                sku_no=item["sku_no"],
                non_print_sku_active=False,
                extra_fields=item["extra_fields"],
            ))
            continue

        p_no = item["product_no"]
        if not p_no:
            rejected_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                status="rejected",
                reason="缺少 product_no",
                sku_no=item["sku_no"],
                non_print_sku_active=True,
                extra_fields=item["extra_fields"],
            ))
            continue

        if p_no in blacklisted_set:
            rejected_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                status="rejected",
                reason="命中黑名单",
                product_no=p_no,
                sku_no=item["sku_no"],
                non_print_sku_active=True,
                extra_fields=item["extra_fields"],
            ))
            continue

        if p_no in whitelisted_set:
            rejected_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                status="rejected",
                reason="命中白名单（按规则不在黑白名单才生效）",
                product_no=p_no,
                sku_no=item["sku_no"],
                non_print_sku_active=True,
                extra_fields=item["extra_fields"],
            ))
            continue

        rec = RawRecordOut(
            barcode=bc,
            status="accepted",
            reason="通过",
            product_no=p_no,
            sku_no=item["sku_no"],
            non_print_sku_active=True,
            extra_fields=item["extra_fields"],
        )
        records.append(rec)
        accepted_records.append(rec)

    by_product_no = build_groups(accepted_records, "product_no")
    by_sku_no = build_groups(accepted_records, "sku_no")

    return ScanFilterOut(
        total_input=len(raw_inputs),
        unique_barcodes=len(unique_barcodes),
        accepted_count=len(accepted_records),
        rejected_count=rejected_count,
        error_count=error_count,
        records=records,
        by_product_no=by_product_no,
        by_sku_no=by_sku_no,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
