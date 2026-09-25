from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.actions import router as actions_router
from app.api.actions import start_evening_verifier
from app.api.nba import router as nba_router
from app.api.predictions import router as predictions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verificación automática antes de que se renueve la cuota (ver actions.py).
    stop = start_evening_verifier()
    yield
    stop.set()


app = FastAPI(title="Football Value Betting API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # frontend Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predictions_router)
app.include_router(actions_router)
app.include_router(nba_router)


@app.get("/health")
def health():
    return {"status": "ok"}
