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


class DynamicResellerClient(BaseResellerClient):
    def __init__(self, provider):
        super().__init__(provider.base_url, provider.api_key)
        self.provider = provider

    def _get_nested(self, data, path):
        if not path:
            return data
        parts = path.split('.')
        for part in parts:
            if isinstance(data, dict):
                data = data.get(part)
            elif isinstance(data, list):
                try:
                    idx = int(part)
                    data = data[idx]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return data

    async def get_products(self) -> List[Dict]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider.purchase_headers:
            try:
                headers = json.loads(self.provider.purchase_headers.replace("{api_key}", self.api_key))
            except Exception:
                pass
        
        url = f"{self.base_url}/{self.provider.products_url.lstrip('/')}"
        
        async with self.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"DynamicResellerClient get_products error: status={resp.status}")
                return []
            data = await resp.json()
            
            products_list = self._get_nested(data, self.provider.products_path) if self.provider.products_path else data
            if not isinstance(products_list, list):
                if isinstance(products_list, dict):
                    for k, v in products_list.items():
                        if isinstance(v, list):
                            products_list = v
                            break
                if not isinstance(products_list, list):
                    logger.error(f"DynamicResellerClient products is not a list: {products_list}")
                    return []
            
            products = []
            for p in products_list:
                try:
                    p_id = str(self._get_nested(p, self.provider.product_id_path or "id"))
                    p_name = str(self._get_nested(p, self.provider.product_name_path or "name"))
                    p_price = Decimal(str(self._get_nested(p, self.provider.product_price_path or "price") or 0))
                    p_stock = int(self._get_nested(p, self.provider.product_stock_path or "stock") or 0)
                    products.append({
                        "id": p_id,
                        "name": p_name,
                        "price": p_price,
                        "stock": p_stock
                    })
                except Exception as e:
                    logger.error(f"DynamicResellerClient failed parsing product item: {p}, error: {e}")
            return products

    async def purchase_product(self, upstream_id: str, quantity: int, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider.purchase_headers:
            try:
                headers_str = self.provider.purchase_headers\
                    .replace("{api_key}", self.api_key)\
                    .replace("{idempotency_key}", idempotency_key)
                headers = json.loads(headers_str)
            except Exception:
                pass

        url = f"{self.base_url}/{self.provider.purchase_url.lstrip('/')}"
        
        payload = {}
        if self.provider.purchase_payload_template:
            try:
                payload_str = self.provider.purchase_payload_template\
                    .replace("{product_id}", upstream_id)\
                    .replace("{quantity}", str(quantity))\
                    .replace("{idempotency_key}", idempotency_key)
                payload = json.loads(payload_str)
            except Exception as e:
                logger.error(f"Failed to parse purchase payload template: {e}")
                payload = {"product_id": upstream_id, "quantity": quantity}
        else:
            payload = {"product_id": upstream_id, "quantity": quantity}

        method = (self.provider.purchase_method or "POST").upper()
        
        if method == "POST":
            async with self.session.post(url, headers=headers, json=payload) as resp:
                status_code = resp.status
                try:
                    data = await resp.json()
                except Exception:
                    data = await resp.text()
        else:
            async with self.session.get(url, headers=headers, params=payload) as resp:
                status_code = resp.status
                try:
                    data = await resp.json()
                except Exception:
                    data = await resp.text()

        if status_code not in (200, 201):
            logger.error(f"Dynamic purchase failed: status={status_code}, response={data}")
            return False, None, str(data)

        if isinstance(data, dict):
            order_id = self._get_nested(data, self.provider.purchase_order_id_path or "order_id")
            if not order_id:
                order_id = data.get("id") or data.get("code")
            
            creds = None
            if self.provider.purchase_credentials_path:
                creds = self._get_nested(data, self.provider.purchase_credentials_path)
            
            if not creds:
                creds = data.get("credentials") or data.get("value") or data.get("accounts") or json.dumps(data)
            
            return True, str(order_id) if order_id else "unknown_order", str(creds)
        else:
            return True, "text_order", str(data)

    async def get_balance(self) -> Decimal:
        return Decimal("0.00")
