import uvicorn


def main() -> None:
    uvicorn.run(
        'obsidian_sync.app:app',
        host='0.0.0.0',  # nosec B104
        port=8000,
        reload=False,
    )
