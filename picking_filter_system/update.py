from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# 假设目录结构为：
# picking_filter_system/
# ├── web_service/main.py
# └── frontend/index.html
FRONTEND_DIR = Path(
    os.getenv("FRONTEND_DIR", Path(__file__).resolve().parent.parent / "frontend")
)

if FRONTEND_DIR.exists():
    # 如果后续有独立的 js/css/图片资源，可挂载到 /static
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"index.html not found at: {index_file}",
        )
    return FileResponse(index_file, media_type="text/html; charset=utf-8")
