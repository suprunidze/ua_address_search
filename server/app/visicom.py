"""Visicom adapter. Do not log upstream request URLs (they contain the key)."""

import logging
from typing import Any, Dict
import httpx
from ua_address_search.core.models import InvalidProviderData, normalize

# httpx logs full URLs at INFO; both parent and child loggers must be silenced.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


class ProviderError(Exception):
    def __init__(self, code: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(code)


class Visicom:
    def __init__(self, key: str, client: httpx.AsyncClient):
        self.key = key
        self.client = client

    async def request(self, path: str, params: Dict[str, Any]):
        if not self.key:
            raise ProviderError("provider_not_configured", 503)
        try:
            response = await self.client.get(
                "https://api.visicom.ua/data-api/5.0/uk/" + path,
                params=dict(params, key=self.key),
            )
            if response.status_code == 429:
                raise ProviderError("provider_quota", 429)
            if response.status_code in (401, 403):
                raise ProviderError("provider_auth", 503)
            if response.status_code != 200:
                raise ProviderError("provider_unavailable")
            if len(response.content) > 5_000_000:
                raise ProviderError("provider_invalid_response")
            return [item.to_dict() for item in normalize(response.json())]
        except httpx.TimeoutException:
            raise ProviderError("provider_timeout", 504) from None
        except httpx.HTTPError:
            raise ProviderError("provider_unavailable") from None
        except (ValueError, InvalidProviderData, TypeError, KeyError):
            raise ProviderError("provider_invalid_response") from None

    async def search(self, text: str):
        return await self.request(
            "geocode.json",
            {
                "text": text,
                "categories": "adr_address,adr_street,adm_settlement",
                "country": "ua",
                "limit": 10,
            },
        )

    async def reverse(self, longitude: float, latitude: float):
        return await self.request(
            "geocode.json",
            {
                "categories": "adr_address",
                "near": f"{longitude},{latitude}",
                "radius": 50,
                "limit": 1,
                "order": "distance",
                "country": "ua",
            },
        )

    async def feature(self, identifier: str):
        return await self.request(f"feature/{identifier}.json", {})
