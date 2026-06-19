import uvicorn
import os

if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )
