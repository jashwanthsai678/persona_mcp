"""FastAPI app: signup/login, persona dashboard + upload, and the multi-tenant
MCP server mounted at /mcp/{token}.

Run:
    uv run uvicorn webapp.main:app --reload
"""

import os
import shutil
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from persona_mcp import config
from webapp import db
from webapp.auth import get_current_user, hash_password, login_user, logout_user, verify_password
from webapp.mcp_multi import mcp as persona_mcp_server
from webapp.processing import process_persona_upload

WEBAPP_DIR = config.REPO_ROOT / "webapp"
templates = Jinja2Templates(directory=str(WEBAPP_DIR / "templates"))

SECRET_KEY = os.environ.get("WEBAPP_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = "dev-only-insecure-secret-key-set-WEBAPP_SECRET_KEY-for-real-use"
    print("WARNING: WEBAPP_SECRET_KEY not set, using an insecure default. Set it before deploying.")

mcp_app = persona_mcp_server.http_app(path="/")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp_app.lifespan(_app):
        yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR / "static")), name="static")
app.mount("/mcp/{token}", mcp_app)


def build_mcp_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/mcp/{token}"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@app.post("/signup")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if db.get_user_by_email(email):
        return templates.TemplateResponse(
            request, "signup.html", {"error": "An account with that email already exists."}, status_code=400
        )
    user = db.create_user(email=email, password_hash=hash_password(password))
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email.strip().lower())
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid email or password."}, status_code=400
        )
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout_submit(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard / personas
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = get_current_user(request)
    if user is None:
        return templates.TemplateResponse(request, "landing.html", {"user": None})
    personas = db.get_personas_for_owner(user.id)
    return templates.TemplateResponse(request, "dashboard.html", {"user": user, "personas": personas})


@app.post("/personas")
def create_persona(request: Request, name: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    persona = db.create_persona(owner_id=user.id, name=name.strip())
    return RedirectResponse(f"/personas/{persona.id}", status_code=303)


@app.get("/personas/{persona_id}", response_class=HTMLResponse)
def persona_detail(request: Request, persona_id: str):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    persona = db.get_persona(persona_id)
    if persona is None or persona.owner_id != user.id:
        return RedirectResponse("/", status_code=303)

    mcp_url = build_mcp_url(request, persona.mcp_token)
    return templates.TemplateResponse(
        request,
        "persona_detail.html",
        {"user": user, "persona": persona, "bio_text": persona.bio_text, "mcp_url": mcp_url},
    )


@app.post("/personas/{persona_id}/bio")
def update_bio(request: Request, persona_id: str, bio: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    persona = db.get_persona(persona_id)
    if persona is None or persona.owner_id != user.id:
        return RedirectResponse("/", status_code=303)
    db.update_persona_bio(persona_id, bio)
    return RedirectResponse(f"/personas/{persona_id}", status_code=303)


@app.post("/personas/{persona_id}/upload")
def upload_files(
    request: Request,
    persona_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    target_sender: str = Form(""),
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    persona = db.get_persona(persona_id)
    if persona is None or persona.owner_id != user.id:
        return RedirectResponse("/", status_code=303)

    raw_dir = config.get_raw_dir(persona.id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for upload in files:
        if not upload.filename:
            continue
        dest = raw_dir / upload.filename
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)

    db.update_persona_status(persona_id, "processing", "Queued for processing...")
    background_tasks.add_task(process_persona_upload, persona.id, persona.name)
    return RedirectResponse(f"/personas/{persona_id}", status_code=303)


@app.post("/personas/{persona_id}/delete")
def delete_persona(request: Request, persona_id: str):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    persona = db.get_persona(persona_id)
    if persona is None or persona.owner_id != user.id:
        return RedirectResponse("/", status_code=303)
    db.delete_persona(persona_id)
    persona_dir = config.get_persona_dir(persona_id)
    if persona_dir.exists():
        shutil.rmtree(persona_dir, ignore_errors=True)
    return RedirectResponse("/", status_code=303)
