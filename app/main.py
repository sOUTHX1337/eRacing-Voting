from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .routers import abstimmen, admin, auth, export, versammlungen, wahlgaenge

app = FastAPI(title="LA eRacing Voting")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(versammlungen.router)
app.include_router(wahlgaenge.router)
app.include_router(abstimmen.router)
app.include_router(export.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup():
    init_db()
