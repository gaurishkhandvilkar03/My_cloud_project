"""HTTP handlers.

Kept separate from function_app.py so they can be unit-tested by passing in
a fake request and an in-memory service, without the Functions runtime.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlparse

import azure.functions as func

from .service import CodeTakenError, LinkService, ValidationError

JSON = "application/json"


def _json(body: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype=JSON)


def _error(message: str, status: int) -> func.HttpResponse:
    return _json({"error": message}, status)


def _short_url(req: func.HttpRequest, code: str) -> str:
    base = os.environ.get("SHORT_URL_BASE")
    if not base:
        parsed = urlparse(req.url)
        base = f"{parsed.scheme}://{parsed.netloc}/api/r"
    return f"{base.rstrip('/')}/{code}"


def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"status": "ok"})


def shorten(req: func.HttpRequest, service: LinkService) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.", 400)

    try:
        link = service.create(body.get("url"), body.get("custom_code"))
    except ValidationError as exc:
        return _error(str(exc), 400)
    except CodeTakenError as exc:
        return _error(f"Code '{exc}' is already in use.", 409)

    logging.info("Created short link %s -> %s", link.code, link.url)
    payload = link.to_dict()
    payload["short_url"] = _short_url(req, link.code)
    return _json(payload, 201)


def redirect(req: func.HttpRequest, service: LinkService) -> func.HttpResponse:
    code = req.route_params.get("code", "")
    link = service.resolve(code)
    if link is None:
        return _error(f"No link found for code '{code}'.", 404)
    return func.HttpResponse(status_code=302, headers={"Location": link.url})


def stats(req: func.HttpRequest, service: LinkService) -> func.HttpResponse:
    code = req.route_params.get("code", "")
    link = service.stats(code)
    if link is None:
        return _error(f"No link found for code '{code}'.", 404)
    payload = link.to_dict()
    payload["short_url"] = _short_url(req, link.code)
    return _json(payload)


def delete(req: func.HttpRequest, service: LinkService) -> func.HttpResponse:
    code = req.route_params.get("code", "")
    if not service.delete(code):
        return _error(f"No link found for code '{code}'.", 404)
    return func.HttpResponse(status_code=204)
