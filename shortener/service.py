"""Business rules: validating URLs, generating codes, creating links."""

from __future__ import annotations

import re
import secrets
import string
from typing import Optional
from urllib.parse import urlparse

from .repository import Link, LinkExistsError, Repository

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
CUSTOM_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
MAX_URL_LENGTH = 2048
RESERVED_CODES = {"api", "admin", "health", "links", "shorten", "stats"}


class ValidationError(ValueError):
    """The request was well-formed JSON but its content is not acceptable."""


class CodeTakenError(Exception):
    """A custom code was requested that already exists."""


def validate_url(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("'url' is required and must be a string.")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError(f"'url' must be at most {MAX_URL_LENGTH} characters.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("'url' must be an absolute http:// or https:// URL.")
    return url


def validate_custom_code(code: object) -> str:
    if not isinstance(code, str) or not CUSTOM_CODE_PATTERN.match(code):
        raise ValidationError(
            "'custom_code' must be 3-32 characters: letters, digits, '-' or '_'."
        )
    if code.lower() in RESERVED_CODES:
        raise ValidationError(f"'{code}' is reserved; choose another code.")
    return code


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


class LinkService:
    MAX_ATTEMPTS = 5

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def create(self, url: object, custom_code: Optional[object] = None) -> Link:
        url = validate_url(url)
        if custom_code is not None:
            code = validate_custom_code(custom_code)
            try:
                link = Link(code=code, url=url)
                self.repo.add(link)
                return link
            except LinkExistsError as exc:
                raise CodeTakenError(code) from exc

        # Random codes: 62^7 ≈ 3.5 trillion possibilities, so collisions are
        # rare, but we still retry a few times rather than overwrite.
        for _ in range(self.MAX_ATTEMPTS):
            link = Link(code=generate_code(), url=url)
            try:
                self.repo.add(link)
                return link
            except LinkExistsError:
                continue
        raise RuntimeError("Could not generate a unique code; try again.")

    def resolve(self, code: str) -> Optional[Link]:
        """Look up a code for a redirect and count the click."""
        return self.repo.increment_clicks(code)

    def stats(self, code: str) -> Optional[Link]:
        return self.repo.get(code)

    def delete(self, code: str) -> bool:
        return self.repo.delete(code)
