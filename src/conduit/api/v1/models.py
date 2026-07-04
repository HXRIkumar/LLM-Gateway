"""OpenAI-compatible model listing endpoint.

Lists the models advertised by all registered providers, in the OpenAI
``{"object": "list", "data": [...]}`` shape. ``owned_by`` names the serving
provider.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from conduit.api.deps import ProviderRegistryDep
from conduit.api.middleware import CurrentPrincipal

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models")
async def list_models(
    principal: CurrentPrincipal,
    registry: ProviderRegistryDep,
) -> dict[str, Any]:
    owners = registry.model_provider_map()
    return {
        "object": "list",
        "data": [
            {
                "id": model.id,
                "object": "model",
                "created": 0,
                "owned_by": owners.get(model.id, "conduit"),
            }
            for model in registry.all_models()
        ],
    }
