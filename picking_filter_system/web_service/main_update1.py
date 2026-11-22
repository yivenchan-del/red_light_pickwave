    parsed_entries: list[dict[str, Any]] = []
    candidate_product_nos: list[str] = []

    for bc, api_res in zip(unique_barcodes, api_results):
        if not api_res["ok"]:
            parsed_entries.append({
                "barcode": bc,
                "api_ok": False,
                "api_error": api_res["error"],
                "candidates": [],
            })
            continue

        candidates = extract_remote_candidates(api_res["data"])
        for c in candidates:
            active = is_non_print_sku_true(c["non_print_sku"])
            c["non_print_sku_active"] = active
            if active and c["product_no"]:
                candidate_product_nos.append(c["product_no"])

        parsed_entries.append({
            "barcode": bc,
            "api_ok": True,
            "candidates": candidates,
        })

    try:
        blacklisted_set, whitelisted_set = await query_db_lists(candidate_product_nos)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to query DB service: {exc}")

    records: list[RawRecordOut] = []
    accepted_records: list[RawRecordOut] = []
    rejected_count = 0
    error_count = 0

    for entry in parsed_entries:
        bc = entry["barcode"]
        if not entry["api_ok"]:
            error_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                item_index=1,
                status="error",
                reason=f"远程API请求失败: {entry['api_error']}",
            ))
            continue

        if not entry["candidates"]:
            rejected_count += 1
            records.append(RawRecordOut(
                barcode=bc,
                item_index=1,
                status="rejected",
                reason="远程API未返回有效记录",
            ))
            continue

        for idx, c in enumerate(entry["candidates"], start=1):
            p_no = c["product_no"]
            if not c["non_print_sku_active"]:
                rejected_count += 1
                records.append(RawRecordOut(
                    barcode=bc,
                    item_index=idx,
                    status="rejected",
                    reason="non_print_sku 为空或非真",
                    product_no=p_no,
                    sku_no=c["sku_no"],
                    non_print_sku_active=False,
                    extra_fields=c["extra_fields"],
                ))
            elif not p_no:
                rejected_count += 1
                records.append(RawRecordOut(
                    barcode=bc,
                    item_index=idx,
                    status="rejected",
                    reason="缺少 product_no",
                    sku_no=c["sku_no"],
                    non_print_sku_active=True,
                    extra_fields=c["extra_fields"],
                ))
            elif p_no in blacklisted_set:
                rejected_count += 1
                records.append(RawRecordOut(
                    barcode=bc,
                    item_index=idx,
                    status="rejected",
                    reason="命中黑名单",
                    product_no=p_no,
                    sku_no=c["sku_no"],
                    non_print_sku_active=True,
                    extra_fields=c["extra_fields"],
                ))
            elif p_no in whitelisted_set:
                rejected_count += 1
                records.append(RawRecordOut(
                    barcode=bc,
                    item_index=idx,
                    status="rejected",
                    reason="命中白名单（按规则不在黑白名单才生效）",
                    product_no=p_no,
                    sku_no=c["sku_no"],
                    non_print_sku_active=True,
                    extra_fields=c["extra_fields"],
                ))
            else:
                rec = RawRecordOut(
                    barcode=bc,
                    item_index=idx,
                    status="accepted",
                    reason="通过",
                    product_no=p_no,
                    sku_no=c["sku_no"],
                    non_print_sku_active=True,
                    extra_fields=c["extra_fields"],
                )
                records.append(rec)
                accepted_records.append(rec)
