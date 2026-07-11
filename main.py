from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# load_dotenv is called once here, before any other local imports that
# rely on os.getenv(). Calling it in cache.py/database.py too was harmless
# but redundant — removed from those modules now.
load_dotenv()

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware  # was missing: without this,
from slowapi.util import get_remote_address       # default_limits did nothing.

from database import Base, engine
from logger_config import get_logger
import locations
import leads
import push

Base.metadata.create_all(bind=engine)
logger = get_logger("main")

LIMITER = Limiter(key_func=get_remote_address, default_limits=["1000/hour"])
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up")
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="Places I've Been",
    lifespan=lifespan,
)

# Middleware — order matters in Starlette: last added = outermost.
app.state.limiter = LIMITER
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(locations.router)
app.include_router(leads.router)
app.include_router(push.router)


@app.get("/sw.js", include_in_schema=False)
def serve_service_worker():
    """
    Service workers must be served from the root path (/) to control
    the whole app. Serving from /static/sw.js would limit the scope
    to /static/ only, breaking push notification handling.
    """
    sw_file = STATIC_DIR / "sw.js"
    if not sw_file.exists():
        return Response(content="sw.js not found", status_code=404)
    return FileResponse(sw_file, media_type="application/javascript")


@app.get("/", include_in_schema=False)
def serve_landing_page():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return Response(
            content="index.html not found — make sure static/index.html exists",
            status_code=404,
        )
    return FileResponse(index_file)
