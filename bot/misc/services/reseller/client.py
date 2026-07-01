import aiohttp
import json
import logging
from decimal import Decimal
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

class BaseResellerClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = aiohttp.ClientSession()

    async def close(self):
        if not self.session.closed:
            await self.session.close()

    async def get_products(self) -> List[Dict]:
        raise NotImplementedError

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        raise NotImplementedError

    async def get_balance(self) -> Decimal:
        raise NotImplementedError


class CatalystClient(BaseResellerClient):
    """
    Catalyst Store Client.
    Auth: Authorization: Bearer sk_live_...
    """
    async def get_products(self) -> List[Dict]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/api/v1/products"
        params = {"limit": "50", "in_stock": "true"}
        async with self.session.get(url, headers=headers, params=params) as resp:
            if resp.status != 200:
                logger.error(f"Catalyst get_products error: status={resp.status}")
                return []
            data = await resp.json()
            products = []
            for p in data.get("products", data):
                products.append({
                    "id": str(p.get("id")),
                    "name": p.get("name"),
                    "price": Decimal(str(p.get("price", 0))),
                    "stock": p.get("stock", 0)
                })
            return products

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": idempotency_key
        }
        url = f"{self.base_url}/api/v1/orders"
        payload = {"product_id": upstream_id, "quantity": quantity}
        async with self.session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                txt = await resp.text()
                logger.error(f"Catalyst purchase failed: status={resp.status}, response={txt}")
                return False, None, txt
            data = await resp.json()
            order_code = data.get("code") or data.get("id")
            
            cred_url = f"{self.base_url}/api/v1/orders/{order_code}/credentials"
            async with self.session.get(cred_url, headers=headers) as cred_resp:
                if cred_resp.status == 200:
                    cred_data = await cred_resp.json()
                    creds = cred_data.get("credentials") or cred_data.get("value") or json.dumps(cred_data)
                    return True, str(order_code), str(creds)
                return True, str(order_code), json.dumps(data)

    async def get_balance(self) -> Decimal:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/api/v1/account/balance"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return Decimal(str(data.get("balance", 0)))
            return Decimal("0.00")


class AIMarketClient(BaseResellerClient):
    """
    AI Market Client.
    Auth: X-API-Key
    """
    async def get_products(self) -> List[Dict]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/products"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"AIMarket get_products error: status={resp.status}")
                return []
            data = await resp.json()
            products = []
            for p in data.get("products", data):
                products.append({
                    "id": str(p.get("id")),
                    "name": p.get("name"),
                    "price": Decimal(str(p.get("price", 0))),
                    "stock": p.get("stock", 0)
                })
            return products

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/purchase"
        payload = {"product_id": upstream_id, "quantity": quantity, "idempotency_key": idempotency_key}
        async with self.session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                txt = await resp.text()
                logger.error(f"AIMarket purchase failed: status={resp.status}, response={txt}")
                return False, None, txt
            data = await resp.json()
            order_id = data.get("order_id") or data.get("id")
            creds = data.get("credentials") or data.get("value") or json.dumps(data)
            return True, str(order_id), str(creds)

    async def get_balance(self) -> Decimal:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/balance"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return Decimal(str(data.get("balance", 0)))
            return Decimal("0.00")


class MMOStoreClient(BaseResellerClient):
    """
    MMO Store Client.
    Auth: X-API-Key
    """
    async def get_products(self) -> List[Dict]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/products"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"MMOStore get_products error: status={resp.status}")
                return []
            data = await resp.json()
            products = []
            for p in data.get("products", data):
                products.append({
                    "id": str(p.get("id")),
                    "name": p.get("name"),
                    "price": Decimal(str(p.get("price", 0))),
                    "stock": p.get("stock", 0)
                })
            return products

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/orders"
        payload = {"product_id": upstream_id, "quantity": quantity, "idempotency_key": idempotency_key}
        async with self.session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                txt = await resp.text()
                logger.error(f"MMOStore purchase failed: status={resp.status}, response={txt}")
                return False, None, txt
            data = await resp.json()
            order_id = data.get("id") or data.get("order_id")
            creds = data.get("accounts") or data.get("value") or json.dumps(data)
            return True, str(order_id), str(creds)

    async def get_balance(self) -> Decimal:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/balance"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return Decimal(str(data.get("balance", 0)))
            return Decimal("0.00")


class CanbosoClient(BaseResellerClient):
    """
    Canboso Client.
    Auth: X-API-Key
    """
    async def get_products(self) -> List[Dict]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/telegram-buyer/products"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"Canboso get_products error: status={resp.status}")
                return []
            data = await resp.json()
            products = []
            for p in data.get("products", data):
                products.append({
                    "id": str(p.get("id")),
                    "name": p.get("name"),
                    "price": Decimal(str(p.get("price", 0))),
                    "stock": p.get("stock", 0)
                })
            return products

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/telegram-buyer/purchase"
        payload = {"product_id": upstream_id, "quantity": quantity, "idempotency_key": idempotency_key}
        async with self.session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                txt = await resp.text()
                logger.error(f"Canboso purchase failed: status={resp.status}, response={txt}")
                return False, None, txt
            data = await resp.json()
            order_id = data.get("purchase_id") or data.get("id")
            creds = data.get("credentials") or data.get("value") or json.dumps(data)
            return True, str(order_id), str(creds)

    async def get_balance(self) -> Decimal:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/telegram-buyer/balance"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return Decimal(str(data.get("balance", 0)))
            return Decimal("0.00")


class BunaiStoreClient(BaseResellerClient):
    """
    Bunai Store Client.
    Auth: X-API-Key
    """
    async def get_products(self) -> List[Dict]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/products"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"BunaiStore get_products error: status={resp.status}")
                return []
            data = await resp.json()
            products = []
            for p in data.get("products", data):
                products.append({
                    "id": str(p.get("id")),
                    "name": p.get("name"),
                    "price": Decimal(str(p.get("price", 0))),
                    "stock": p.get("stock", 0)
                })
            return products

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/purchase"
        payload = {"product_id": upstream_id, "quantity": quantity, "idempotency_key": idempotency_key}
        async with self.session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                txt = await resp.text()
                logger.error(f"BunaiStore purchase failed: status={resp.status}, response={txt}")
                return False, None, txt
            data = await resp.json()
            order_id = data.get("id") or data.get("purchase_id")
            creds = data.get("credentials") or data.get("value") or json.dumps(data)
            return True, str(order_id), str(creds)

    async def get_balance(self) -> Decimal:
        headers = {"X-API-Key": self.api_key}
        url = f"{self.base_url}/api/v1/balance"
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return Decimal(str(data.get("balance", 0)))
            return Decimal("0.00")
