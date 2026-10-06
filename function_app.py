"""Azure Functions entry point (Python v2 programming model).

Endpoints (all under /api):
    GET    /health              liveness check
    POST   /shorten             create a short link       {"url": ..., "custom_code"?: ...}
    GET    /r/{code}            302 redirect to the original URL (counts a click)
    GET    /stats/{code}        link details and click count
    DELETE /links/{code}        delete a link (requires a function key)
"""

import os

import azure.functions as func

from shortener import handlers
from shortener.repository import InMemoryRepository, TableRepository
from shortener.service import LinkService

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_service = None


def get_service() -> LinkService:
    """Create the service once per worker and reuse it (avoids reconnecting
    to storage on every invocation, which keeps cold-path latency down)."""
    global _service
    if _service is None:
        conn = os.environ.get("LINKS_STORAGE_CONNECTION") or os.environ.get(
            "AzureWebJobsStorage"
        )
        if conn:
            repo = TableRepository(conn, os.environ.get("LINKS_TABLE_NAME", "links"))
        else:
            repo = InMemoryRepository()  # demo mode: data lost on restart
        _service = LinkService(repo)
    return _service


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return handlers.health(req)


@app.route(route="shorten", methods=["POST"])
def shorten(req: func.HttpRequest) -> func.HttpResponse:
    return handlers.shorten(req, get_service())


@app.route(route="r/{code}", methods=["GET"])
def redirect(req: func.HttpRequest) -> func.HttpResponse:
    return handlers.redirect(req, get_service())


@app.route(route="stats/{code}", methods=["GET"])
def stats(req: func.HttpRequest) -> func.HttpResponse:
    return handlers.stats(req, get_service())


@app.route(route="links/{code}", methods=["DELETE"], auth_level=func.AuthLevel.FUNCTION)
def delete(req: func.HttpRequest) -> func.HttpResponse:
    return handlers.delete(req, get_service())
