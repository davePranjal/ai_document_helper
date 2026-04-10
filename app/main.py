from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import chat, documents, health, insights, metrics
from app.config import settings
from app.logging_config import setup_logging
from app.middleware import RequestIDMiddleware, limiter

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure upload directory exists
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="AI Document Vault",
    description="AI-Powered Document Management System with RAG-based Chat",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware (order matters: last added = first executed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)

# Routers
app.include_router(health.router, tags=["Health"])
app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
app.include_router(insights.router, prefix="/api/v1", tags=["Insights"])
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(metrics.router, prefix="/api/v1", tags=["Metrics"])

# Static UI
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/ui/")
