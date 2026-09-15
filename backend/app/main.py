from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.predictions import router as predictions_router

app = FastAPI(title="Football Value Betting API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # frontend Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predictions_router)


@app.get("/health")
def health():
    return {"status": "ok"}
