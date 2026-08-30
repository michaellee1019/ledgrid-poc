"""HTTP contract for revisioned managed installation-profile authoring.

The profile authoring service is deliberately independent from a Flask
application.  This module is the narrow HTTP adapter for it: all mutations
require the current opaque draft revision, publishing only creates an immutable
candidate, and activation remains the responsibility of the guarded scene
transaction.  Keeping that separation prevents a profile edit from selecting
geometry on the live wall as a side effect.
"""

from __future__ import annotations

from copy import deepcopy
from flask import Blueprint, Flask, Response, jsonify, request

from animation.core.installation_profile_authoring import (
    InstallationProfileAuthoring,
    InstallationProfileAuthoringError,
    InstallationProfileDraftConflict,
)
from animation.core.installation_profile_library import (
    InstallationProfileLibraryError,
    InstallationProfileNotFoundError,
)


def _if_match_revision() -> str | None:
    """Return one opaque revision token from a strict single-value If-Match."""

    raw = request.headers.get("If-Match")
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    # A list and the wildcard are not meaningful for a single draft revision.
    if value == "*" or "," in value:
        return None
    return value


def _draft_response(draft: dict[str, object]) -> Response:
    response = jsonify(draft)
    response.headers["ETag"] = f'"{draft["revision"]}"'
    response.headers["Cache-Control"] = "no-store"
    return response


def _conflict_response(conflict: InstallationProfileDraftConflict) -> Response:
    response = jsonify(
        {
            "error": str(conflict),
            "code": "revision_conflict",
            "current_revision": conflict.current_revision,
        }
    )
    response.status_code = 409
    response.headers["ETag"] = f'"{conflict.current_revision}"'
    response.headers["Cache-Control"] = "no-store"
    return response


def _authoring_error_response(error: Exception) -> tuple[Response, int]:
    if isinstance(error, InstallationProfileNotFoundError):
        return jsonify({"error": str(error)}), 404
    if isinstance(error, InstallationProfileAuthoringError):
        return jsonify({"error": str(error)}), 400
    return jsonify({"error": str(error)}), 500


def installation_profile_activation_intent(
    *,
    source_digest: str,
    published_digest: str,
) -> dict[str, str]:
    """Return the profile part of a later guarded scene activation request.

    Publishing is intentionally not activation.  The scene API must include
    this immutable candidate digest in its normal Check/compare-and-swap flow
    before the wall can select it.
    """

    return {
        "source_installation_profile_digest": source_digest,
        "installation_profile_digest": published_digest,
    }


def register_installation_profile_api(
    app: Flask,
    authoring: InstallationProfileAuthoring,
    *,
    url_prefix: str = "/api/v1/installation-profiles",
) -> Blueprint:
    """Register the canonical draft, publish, and artifact endpoints.

    This adapter purposely has no endpoint that selects a profile.  A caller
    receives a content-addressed candidate and sends it through the existing
    guarded scene-activation transaction separately.
    """

    api = Blueprint("installation_profile_api", __name__)

    @api.get("/<digest>/draft")
    def get_draft(digest: str) -> Response | tuple[Response, int]:
        try:
            return _draft_response(authoring.load(digest))
        except (
            InstallationProfileNotFoundError,
            InstallationProfileAuthoringError,
            InstallationProfileLibraryError,
        ) as error:
            return _authoring_error_response(error)

    @api.put("/<digest>/draft")
    def save_draft(digest: str) -> Response | tuple[Response, int]:
        expected_revision = _if_match_revision()
        if expected_revision is None:
            return jsonify(
                {
                    "error": "If-Match is required for installation-profile draft updates",
                    "code": "precondition_required",
                }
            ), 428
        draft = request.get_json(silent=True)
        if draft is None:
            return jsonify({"error": "A complete JSON draft is required"}), 400
        try:
            return _draft_response(
                authoring.update(
                    digest,
                    expected_revision=expected_revision,
                    draft=draft,
                )
            )
        except InstallationProfileDraftConflict as conflict:
            return _conflict_response(conflict)
        except (
            InstallationProfileNotFoundError,
            InstallationProfileAuthoringError,
            InstallationProfileLibraryError,
        ) as error:
            return _authoring_error_response(error)

    @api.post("/<digest>/publish")
    def publish_draft(digest: str) -> Response | tuple[Response, int]:
        expected_revision = _if_match_revision()
        if expected_revision is None:
            return jsonify(
                {
                    "error": "If-Match is required for installation-profile publication",
                    "code": "precondition_required",
                }
            ), 428
        try:
            receipt, draft = authoring.publish(
                digest, expected_revision=expected_revision
            )
        except InstallationProfileDraftConflict as conflict:
            return _conflict_response(conflict)
        except (
            InstallationProfileNotFoundError,
            InstallationProfileAuthoringError,
            InstallationProfileLibraryError,
        ) as error:
            return _authoring_error_response(error)
        response = jsonify(
            {
                "published_digest": receipt.content_digest,
                "artifact_url": f"{url_prefix}/{receipt.content_digest}/artifact",
                "activation_intent": installation_profile_activation_intent(
                    source_digest=digest,
                    published_digest=receipt.content_digest,
                ),
                "selected": False,
                "revision": draft["revision"],
                "receipt": receipt.to_dict(),
            }
        )
        response.headers["ETag"] = f'"{draft["revision"]}"'
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.get("/<digest>/artifact")
    def get_artifact(digest: str) -> Response | tuple[Response, int]:
        try:
            resolved = authoring.library.resolve(digest)
        except (
            InstallationProfileNotFoundError,
            InstallationProfileAuthoringError,
            InstallationProfileLibraryError,
        ) as error:
            return _authoring_error_response(error)
        if request.if_none_match.contains(resolved.content_digest):
            response = app.response_class(status=304)
        else:
            response = app.response_class(
                resolved.encoded,
                status=200,
                mimetype="application/octet-stream",
            )
        response.headers["ETag"] = f'"{resolved.content_digest}"'
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers["X-Installation-Profile-Digest"] = resolved.content_digest
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.register_blueprint(api, url_prefix=url_prefix)
    return api


def undo_draft(
    authoring: InstallationProfileAuthoring,
    digest: str,
    *,
    expected_revision: str,
    historical_draft: dict[str, object],
) -> dict[str, object]:
    """Save a client-held historical document as the next revision.

    Undo is deliberately an ordinary optimistic-concurrency save, so it stays
    restart-safe and cannot overwrite an edit that landed after the client
    loaded its current revision.
    """

    candidate = deepcopy(historical_draft)
    candidate["revision"] = expected_revision
    return authoring.update(
        digest, expected_revision=expected_revision, draft=candidate
    )


__all__ = [
    "installation_profile_activation_intent",
    "register_installation_profile_api",
    "undo_draft",
]
