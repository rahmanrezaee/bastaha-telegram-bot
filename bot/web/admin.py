import logging
import time
from typing import Any

from sqladmin import Admin, ModelView, BaseView, expose
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.background import BackgroundTasks
from starlette.routing import Route
from sqlalchemy import text

from markupsafe import Markup

from bot.misc import EnvKeys
from bot.database.methods.audit import log_audit

logger = logging.getLogger(__name__)


class LoginRateLimiter:
    """In-memory rate limiter for login attempts by IP."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._attempts: dict[str, list[float]] = {}
        self._last_cleanup: float = time.time()

    def is_blocked(self, ip: str) -> bool:
        if ip not in self._attempts:
            return False
        now = time.time()
        self._attempts[ip] = [t for t in self._attempts[ip] if now - t < self.lockout_seconds]
        return len(self._attempts[ip]) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        now = time.time()
        if now - self._last_cleanup > 600:
            self._attempts = {
                k: [t for t in v if now - t < self.lockout_seconds]
                for k, v in self._attempts.items()
                if any(now - t < self.lockout_seconds for t in v)
            }
            self._last_cleanup = now
        if ip not in self._attempts:
            self._attempts[ip] = []
        self._attempts[ip].append(now)

    def reset(self, ip: str) -> None:
        self._attempts.pop(ip, None)


_login_limiter = LoginRateLimiter()
from bot.database.main import Database
from bot.database.models.main import (
    User, Role, Goods, ItemValues,
    BoughtGoods, Operations, Payments, ReferralEarnings,
    AuditLog, PromoCodes, Reviews,
    ResellerProviders, ResellerProducts, ResellerOrders,
)
from bot.misc.metrics import get_metrics
from bot.misc.caching import get_cache_manager


# Authentication
class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        ip = request.client.host

        if _login_limiter.is_blocked(ip):
            await log_audit("web_login_blocked", level="WARNING", details=f"ip={ip}", ip_address=ip)
            return False

        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        if username == EnvKeys.ADMIN_USERNAME and password == EnvKeys.ADMIN_PASSWORD:
            if (
                username == "admin" and password == "admin"
                and ip not in ("127.0.0.1", "::1", "localhost")
            ):
                await log_audit("web_login_blocked_default_creds", level="WARNING", details=f"ip={ip}", ip_address=ip)
                return False
            request.session.update({"authenticated": True})
            _login_limiter.reset(ip)
            await log_audit("web_login", user_id=None, details=f"user={username}", ip_address=ip)
            return True

        _login_limiter.record_failure(ip)
        await log_audit("web_login_failed", level="WARNING", details=f"user={username}", ip_address=ip)
        return False

    async def logout(self, request: Request) -> bool:
        await log_audit("web_logout", ip_address=request.client.host)
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)


def _safe_model_repr(model: Any, max_len: int = 500) -> str:
    """Return a truncated repr that excludes sensitive fields."""
    _sensitive = {"balance", "password", "secret", "token", "value"}
    parts = []
    for col in getattr(model, "__table__", None).columns if hasattr(model, "__table__") else ():
        if col.name in _sensitive:
            continue
        val = getattr(model, col.name, None)
        parts.append(f"{col.name}={val!r}")
    result = f"{type(model).__name__}({', '.join(parts)})"
    return result[:max_len]


# Audited base view for mutable models
class AuditModelView(ModelView):
    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        action = f"sqladmin_{'create' if is_created else 'update'}"
        await log_audit(
            action,
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=request.client.host,
        )

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await log_audit(
            "sqladmin_delete",
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=request.client.host,
        )


# Model Views
class UserAdmin(AuditModelView, model=User):
    column_list = [User.telegram_id, User.balance, User.role_id, User.referral_id,
                   User.registration_date, User.is_blocked]
    column_searchable_list = [User.telegram_id]
    column_sortable_list = [User.telegram_id, User.balance, User.registration_date]
    column_default_sort = (User.registration_date, True)
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"


_PERM_FLAGS = [
    (1,   "USE"),
    (2,   "BROADCAST"),
    (4,   "SETTINGS"),
    (8,   "USERS"),
    (16,  "CATALOG"),
    (32,  "ADMINS"),
    (64,  "OWNER"),
    (128, "STATS"),
    (256, "BALANCE"),
    (512, "PROMOS"),
]


def _format_perms_html(model, name):
    perms = getattr(model, name, 0) or 0
    if not perms:
        return Markup('<span style="color:#999">\u2014</span>')
    badges = []
    for bit, label in _PERM_FLAGS:
        if perms & bit:
            badges.append(
                f'<span style="display:inline-block;background:#e2e8f0;padding:1px 6px;'
                f'border-radius:4px;margin:1px;font-size:12px">{label}</span>'
            )
    raw = f'<span style="color:#999;font-size:11px;margin-left:4px">({perms})</span>'
    return Markup(" ".join(badges) + raw)


class RoleAdmin(AuditModelView, model=Role):
    column_list = [Role.id, Role.name, Role.default, Role.permissions]
    column_details_exclude_list = ["users"]
    column_sortable_list = [Role.id, Role.name]
    name = "Role"
    name_plural = "Roles"
    icon = "fa-solid fa-shield-halved"
    column_formatters = {"permissions": _format_perms_html}
    column_formatters_detail = {"permissions": _format_perms_html}
    form_args = {
        "permissions": {
            "description": (
                "Bitmask value — sum the flags you need: "
                "USE=1, BROADCAST=2, SETTINGS=4, USERS=8, CATALOG=16, ADMINS=32, "
                "OWNER=64, STATS=128, BALANCE=256, PROMOS=512. "
                "Example: 927 = full Admin, 1023 = all (Owner)."
            ),
        },
    }


class GoodsAdmin(AuditModelView, model=Goods):
    column_list = [Goods.id, Goods.name, Goods.price, Goods.description]
    column_searchable_list = [Goods.name]
    column_sortable_list = [Goods.id, Goods.name, Goods.price]
    name = "Product"
    name_plural = "Products"
    icon = "fa-solid fa-box"


class ItemValuesAdmin(AuditModelView, model=ItemValues):
    column_list = [ItemValues.id, ItemValues.item_id, ItemValues.value, ItemValues.is_infinity]
    column_searchable_list = [ItemValues.value]
    column_sortable_list = [ItemValues.id, ItemValues.item_id]
    name = "Stock Item"
    name_plural = "Stock Items"
    icon = "fa-solid fa-warehouse"


class BoughtGoodsAdmin(ModelView, model=BoughtGoods):
    column_list = [BoughtGoods.id, BoughtGoods.item_name, BoughtGoods.value,
                   BoughtGoods.price, BoughtGoods.buyer_id, BoughtGoods.bought_datetime,
                   BoughtGoods.unique_id]
    column_searchable_list = [BoughtGoods.item_name, BoughtGoods.buyer_id, BoughtGoods.unique_id]
    column_sortable_list = [BoughtGoods.id, BoughtGoods.bought_datetime, BoughtGoods.price]
    column_default_sort = (BoughtGoods.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Purchase"
    name_plural = "Purchases"
    icon = "fa-solid fa-cart-shopping"


class OperationsAdmin(ModelView, model=Operations):
    column_list = [Operations.id, Operations.user_id, Operations.operation_value,
                   Operations.operation_time]
    column_searchable_list = [Operations.user_id]
    column_sortable_list = [Operations.id, Operations.operation_time, Operations.operation_value]
    column_default_sort = (Operations.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Operation"
    name_plural = "Operations"
    icon = "fa-solid fa-money-bill-transfer"


class PaymentsAdmin(ModelView, model=Payments):
    column_list = [Payments.id, Payments.provider, Payments.external_id, Payments.user_id,
                   Payments.amount, Payments.currency, Payments.status, Payments.created_at]
    column_searchable_list = [Payments.user_id, Payments.external_id, Payments.provider]
    column_sortable_list = [Payments.id, Payments.created_at, Payments.amount, Payments.status]
    column_default_sort = (Payments.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Payment"
    name_plural = "Payments"
    icon = "fa-solid fa-credit-card"


class ReferralEarningsAdmin(ModelView, model=ReferralEarnings):
    column_list = [ReferralEarnings.id, ReferralEarnings.referrer_id,
                   ReferralEarnings.referral_id, ReferralEarnings.amount,
                   ReferralEarnings.original_amount, ReferralEarnings.created_at]
    column_searchable_list = [ReferralEarnings.referrer_id, ReferralEarnings.referral_id]
    column_sortable_list = [ReferralEarnings.id, ReferralEarnings.created_at, ReferralEarnings.amount]
    column_default_sort = (ReferralEarnings.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Referral Earning"
    name_plural = "Referral Earnings"
    icon = "fa-solid fa-handshake"


class AuditLogAdmin(ModelView, model=AuditLog):
    column_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.user_id,
                   AuditLog.action, AuditLog.resource_type, AuditLog.resource_id,
                   AuditLog.details, AuditLog.ip_address]
    column_searchable_list = [AuditLog.action, AuditLog.resource_type, AuditLog.details]
    column_sortable_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.action]
    column_default_sort = (AuditLog.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-clipboard-list"


class PromoCodeAdmin(AuditModelView, model=PromoCodes):
    column_list = [PromoCodes.id, PromoCodes.code, PromoCodes.discount_type,
                   PromoCodes.discount_value, PromoCodes.max_uses, PromoCodes.current_uses,
                   PromoCodes.is_active, PromoCodes.expires_at, PromoCodes.created_at]
    column_searchable_list = [PromoCodes.code]
    column_sortable_list = [PromoCodes.id, PromoCodes.code, PromoCodes.created_at]
    column_default_sort = (PromoCodes.id, True)
    name = "Promo Code"
    name_plural = "Promo Codes"
    icon = "fa-solid fa-tag"


class ReviewsAdmin(AuditModelView, model=Reviews):
    column_list = [Reviews.id, Reviews.user_id, Reviews.item_name,
                   Reviews.rating, Reviews.text, Reviews.created_at]
    column_searchable_list = [Reviews.user_id, Reviews.item_name]
    column_sortable_list = [Reviews.id, Reviews.rating, Reviews.created_at]
    column_default_sort = (Reviews.id, True)
    name = "Review"
    name_plural = "Reviews"
    icon = "fa-solid fa-star"


class ResellerProvidersAdmin(AuditModelView, model=ResellerProviders):
    column_list = [ResellerProviders.id, ResellerProviders.name, ResellerProviders.base_url,
                   ResellerProviders.is_active, ResellerProviders.markup_percent]
    column_searchable_list = [ResellerProviders.name]
    column_sortable_list = [ResellerProviders.id, ResellerProviders.name, ResellerProviders.markup_percent]
    form_columns = [
        ResellerProviders.name, ResellerProviders.base_url, ResellerProviders.api_key,
        ResellerProviders.is_active, ResellerProviders.markup_percent,
        ResellerProviders.products_url, ResellerProviders.products_path,
        ResellerProviders.product_id_path, ResellerProviders.product_name_path,
        ResellerProviders.product_price_path, ResellerProviders.product_stock_path,
        ResellerProviders.purchase_url, ResellerProviders.purchase_method,
        ResellerProviders.purchase_payload_template, ResellerProviders.purchase_headers,
        ResellerProviders.purchase_order_id_path, ResellerProviders.purchase_credentials_path
    ]
    name = "Reseller Provider"
    name_plural = "Reseller Providers"
    icon = "fa-solid fa-plug"


class ResellerProductsAdmin(AuditModelView, model=ResellerProducts):
    column_list = [ResellerProducts.id, ResellerProducts.provider_id, ResellerProducts.upstream_id,
                   ResellerProducts.name, ResellerProducts.original_price, ResellerProducts.stock,
                   ResellerProducts.mapped_goods_id]
    column_searchable_list = [ResellerProducts.name, ResellerProducts.upstream_id]
    column_sortable_list = [ResellerProducts.id, ResellerProducts.name, ResellerProducts.original_price, ResellerProducts.stock]
    name = "Reseller Product"
    name_plural = "Reseller Products"
    icon = "fa-solid fa-tags"


class ResellerOrdersAdmin(ModelView, model=ResellerOrders):
    column_list = [ResellerOrders.id, ResellerOrders.bought_goods_id, ResellerOrders.provider_id,
                   ResellerOrders.upstream_product_id, ResellerOrders.idempotency_key, ResellerOrders.status,
                   ResellerOrders.upstream_order_id, ResellerOrders.created_at]
    column_searchable_list = [ResellerOrders.idempotency_key, ResellerOrders.upstream_order_id]
    column_sortable_list = [ResellerOrders.id, ResellerOrders.status, ResellerOrders.created_at]
    name = "Reseller Order"
    name_plural = "Reseller Orders"
    icon = "fa-solid fa-receipt"


class ResellerSyncAdmin(BaseView):
    name = "Sync API Products"
    icon = "fa-solid fa-rotate"

    @expose("/sync_catalog", methods=["GET"])
    async def sync_catalog_get(self, request: Request) -> HTMLResponse:
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/admin/login", status_code=303)
        
        from sqlalchemy import select
        async with Database().session() as session:
            res = await session.execute(select(ResellerProviders).order_by(ResellerProviders.name))
            providers = res.scalars().all()
            
        providers_data = []
        for p in providers:
            providers_data.append({
                "id": p.id,
                "name": p.name,
                "base_url": p.base_url,
                "api_key": p.api_key,
                "markup_percent": float(p.markup_percent),
                "products_url": p.products_url or "",
                "products_path": p.products_path or "",
                "product_id_path": p.product_id_path or "",
                "product_name_path": p.product_name_path or "",
                "product_price_path": p.product_price_path or "",
                "product_stock_path": p.product_stock_path or "",
                "purchase_url": p.purchase_url or "",
                "purchase_method": p.purchase_method or "POST",
                "purchase_payload_template": p.purchase_payload_template or "",
                "purchase_headers": p.purchase_headers or "",
                "purchase_order_id_path": p.purchase_order_id_path or "",
                "purchase_credentials_path": p.purchase_credentials_path or ""
            })
            
        import json
        html = get_sync_catalog_html(json.dumps(providers_data))
        return HTMLResponse(html)

    @expose("/preview_sync", methods=["POST"])
    async def preview_sync(self, request: Request) -> JSONResponse:
        if not request.session.get("authenticated"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
            
        data = await request.json()
        provider_id = data.get("provider_id")
        
        from sqlalchemy import select
        from decimal import Decimal
        async with Database().session() as session:
            stmt = select(ResellerProviders).where(ResellerProviders.id == provider_id)
            provider = (await session.execute(stmt)).scalar_one_or_none()
            
        if not provider:
            return JSONResponse({"error": "Provider not found"}, status_code=404)
            
        # Temporarily apply mapping from request if sent, to allow live testing/tuning
        temp_provider = ResellerProviders(
            base_url=data.get("base_url", provider.base_url),
            api_key=data.get("api_key", provider.api_key),
            markup_percent=Decimal(str(data.get("markup_percent", provider.markup_percent))),
            products_url=data.get("products_url", provider.products_url),
            products_path=data.get("products_path", provider.products_path),
            product_id_path=data.get("product_id_path", provider.product_id_path),
            product_name_path=data.get("product_name_path", provider.product_name_path),
            product_price_path=data.get("product_price_path", provider.product_price_path),
            product_stock_path=data.get("product_stock_path", provider.product_stock_path)
        )
        
        import aiohttp
        import json
        headers = {"Authorization": f"Bearer {temp_provider.api_key}"}
        if data.get("purchase_headers"):
            try:
                headers = json.loads(data.get("purchase_headers").replace("{api_key}", temp_provider.api_key))
            except Exception:
                pass
                
        url = f"{temp_provider.base_url.rstrip('/')}/{temp_provider.products_url.lstrip('/')}"
        
        raw_text = ""
        raw_json = None
        parsed_products = []
        error = None
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    raw_text = await resp.text()
                    try:
                        raw_json = json.loads(raw_text)
                    except Exception:
                        raw_json = {"error": "Response is not valid JSON", "raw_response": raw_text}
                        
                    if resp.status == 200 and isinstance(raw_json, (dict, list)):
                        from bot.misc.services.reseller.client import DynamicResellerClient
                        client = DynamicResellerClient(temp_provider)
                        products_list = client._get_nested(raw_json, temp_provider.products_path) if temp_provider.products_path else raw_json
                        
                        if not isinstance(products_list, list):
                            if isinstance(products_list, dict):
                                for k, v in products_list.items():
                                    if isinstance(v, list):
                                        products_list = v
                                        break
                                        
                        if isinstance(products_list, list):
                            for p in products_list:
                                try:
                                    p_id = str(client._get_nested(p, temp_provider.product_id_path or "id"))
                                    p_name = str(client._get_nested(p, temp_provider.product_name_path or "name"))
                                    p_price = float(str(client._get_nested(p, temp_provider.product_price_path or "price") or 0))
                                    p_stock = int(client._get_nested(p, temp_provider.product_stock_path or "stock") or 0)
                                    
                                    markup = 1.0 + (float(temp_provider.markup_percent) / 100.0)
                                    final_price = p_price * markup
                                    
                                    parsed_products.append({
                                        "id": p_id,
                                        "name": p_name,
                                        "original_price": p_price,
                                        "final_price": round(final_price, 2),
                                        "stock": p_stock
                                    })
                                except Exception as e:
                                    logger.error(f"Failed parsing item {p}: {e}")
                        else:
                            error = f"Located products is not a list. Path: {temp_provider.products_path}"
                    else:
                        error = f"HTTP Error status: {resp.status}"
        except Exception as e:
            error = f"Connection failed: {e}"
            raw_json = {"error": str(e)}
            
        return JSONResponse({
            "raw_response": raw_json,
            "mapped_products": parsed_products,
            "error": error
        })

    @expose("/run_sync", methods=["POST"])
    async def run_sync(self, request: Request) -> JSONResponse:
        if not request.session.get("authenticated"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
            
        data = await request.json()
        provider_id = data.get("provider_id")
        
        from sqlalchemy import select
        from decimal import Decimal
        async with Database().session() as session:
            stmt = select(ResellerProviders).where(ResellerProviders.id == provider_id)
            provider = (await session.execute(stmt)).scalar_one_or_none()
            
            if not provider:
                return JSONResponse({"error": "Provider not found"}, status_code=404)
                
            provider.base_url = data.get("base_url", provider.base_url)
            provider.api_key = data.get("api_key", provider.api_key)
            provider.markup_percent = Decimal(str(data.get("markup_percent", provider.markup_percent)))
            provider.products_url = data.get("products_url")
            provider.products_path = data.get("products_path")
            provider.product_id_path = data.get("product_id_path")
            provider.product_name_path = data.get("product_name_path")
            provider.product_price_path = data.get("product_price_path")
            provider.product_stock_path = data.get("product_stock_path")
            
            provider.purchase_url = data.get("purchase_url")
            provider.purchase_method = data.get("purchase_method", "POST")
            provider.purchase_payload_template = data.get("purchase_payload_template")
            provider.purchase_headers = data.get("purchase_headers")
            provider.purchase_order_id_path = data.get("purchase_order_id_path")
            provider.purchase_credentials_path = data.get("purchase_credentials_path")
            
            await session.commit()
            
        from bot.misc.services.reseller import sync_reseller_products
        try:
            await sync_reseller_products()
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Catalog sync failed: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)


def get_sync_catalog_html(providers_json: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sync API Products - Admin Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: #0b0f19;
            background-image: radial-gradient(circle at top right, rgba(99, 102, 241, 0.12), transparent 400px),
                              radial-gradient(circle at bottom left, rgba(168, 85, 247, 0.12), transparent 400px);
        }}
        .glass-panel {{
            background: rgba(17, 24, 39, 0.7);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
    </style>
</head>
<body class="text-slate-100 min-h-screen flex flex-col">
    <header class="glass-panel sticky top-0 z-50 px-6 py-4 flex items-center justify-between border-b border-white/5">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 bg-indigo-500/10 rounded-xl flex items-center justify-center border border-indigo-500/30">
                <i class="fa-solid fa-rotate text-indigo-400 text-lg"></i>
            </div>
            <div>
                <h1 class="text-lg font-bold text-white tracking-tight">Sync API Products</h1>
                <p class="text-xs text-slate-400">Configure mappings, test API feeds, and synchronize upstream products</p>
            </div>
        </div>
        <a href="/admin/reseller-products/list" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium rounded-xl transition duration-200 flex items-center gap-2 border border-white/5">
            <i class="fa-solid fa-arrow-left"></i> Back to Catalog
        </a>
    </header>

    <main class="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        <section class="lg:col-span-5 flex flex-col gap-6">
            <div class="glass-panel p-6 rounded-2xl flex flex-col gap-5">
                <h2 class="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                    <i class="fa-solid fa-sliders text-indigo-400"></i> Select Provider & Parameters
                </h2>
                
                <div>
                    <label class="block text-xs font-semibold text-slate-400 mb-2 uppercase">Reseller Provider</label>
                    <select id="provider-select" onchange="onProviderChange()" class="w-full bg-slate-900 border border-white/10 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 transition">
                        <option value="">-- Choose Provider --</option>
                    </select>
                </div>

                <div id="mapping-fields" class="hidden flex flex-col gap-5">
                    <div class="border-t border-white/5 pt-4">
                        <h3 class="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-3">Connection</h3>
                        <div class="grid grid-cols-1 gap-3">
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Base URL</label>
                                <input type="text" id="base_url" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="https://api.provider.com">
                            </div>
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">API Key / Token</label>
                                <input type="password" id="api_key" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
                            </div>
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Markup Percentage (%)</label>
                                <input type="number" id="markup_percent" step="0.01" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="0.00">
                            </div>
                        </div>
                    </div>

                    <div class="border-t border-white/5 pt-4">
                        <h3 class="text-xs font-bold text-emerald-400 uppercase tracking-wider mb-3">Products Sync Config</h3>
                        <div class="grid grid-cols-1 gap-3">
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Products URL Path</label>
                                <input type="text" id="products_url" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="/api/v1/products">
                            </div>
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Products Selector (JSON Path to list)</label>
                                <input type="text" id="products_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="data.products (leave empty if root list)">
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">ID Path</label>
                                    <input type="text" id="product_id_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="id">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Name Path</label>
                                    <input type="text" id="product_name_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="name">
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Price Path</label>
                                    <input type="text" id="product_price_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="price">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Stock Path</label>
                                    <input type="text" id="product_stock_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="stock">
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="border-t border-white/5 pt-4">
                        <h3 class="text-xs font-bold text-purple-400 uppercase tracking-wider mb-3">Order / Purchase API Config</h3>
                        <div class="grid grid-cols-1 gap-3">
                            <div class="grid grid-cols-3 gap-2">
                                <div class="col-span-2">
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Purchase URL Path</label>
                                    <input type="text" id="purchase_url" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="/api/v1/orders">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Method</label>
                                    <select id="purchase_method" class="w-full bg-slate-955 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
                                        <option value="POST">POST</option>
                                        <option value="GET">GET</option>
                                    </select>
                                </div>
                            </div>
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Custom Headers (JSON)</label>
                                <textarea id="purchase_headers" rows="2" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500" placeholder='{{"Authorization": "Bearer {{api_key}}", "Idempotency-Key": "{{idempotency_key}}"}}'></textarea>
                            </div>
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Payload Template (JSON)</label>
                                <textarea id="purchase_payload_template" rows="2" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500" placeholder='{{"product_id": "{{product_id}}", "quantity": {{quantity}}}}'></textarea>
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Order Code/ID Path</label>
                                    <input type="text" id="purchase_order_id_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="code">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-400 mb-1">Credentials Path</label>
                                    <input type="text" id="purchase_credentials_path" class="w-full bg-slate-955/60 border border-white/5 rounded-lg px-2 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" placeholder="credentials">
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="border-t border-white/5 pt-4 flex gap-3">
                        <button type="button" onclick="getPreview()" class="flex-1 py-3 px-4 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-xl transition duration-150 flex items-center justify-center gap-2 shadow-lg shadow-indigo-600/10">
                            <i class="fa-solid fa-magnifying-glass"></i> Get Raw Data & Preview
                        </button>
                        <button type="button" onclick="runSync()" class="py-3 px-4 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs rounded-xl transition duration-150 flex items-center justify-center gap-2 shadow-lg shadow-emerald-600/10">
                            <i class="fa-solid fa-cloud-arrow-up"></i> Save & Sync DB
                        </button>
                    </div>
                </div>
            </div>
        </section>

        <section class="lg:col-span-7 flex flex-col gap-6">
            <div class="glass-panel p-6 rounded-2xl flex-1 flex flex-col gap-4 min-h-[500px]">
                <div class="flex items-center justify-between border-b border-white/5 pb-2">
                    <div class="flex gap-2">
                        <button id="tab-preview" onclick="switchTab('preview')" class="px-4 py-2 border-b-2 border-indigo-500 text-indigo-400 font-semibold text-xs uppercase tracking-wider transition font-bold">
                            Mapped Objects (Preview)
                        </button>
                        <button id="tab-raw" onclick="switchTab('raw')" class="px-4 py-2 border-b-2 border-transparent text-slate-400 font-semibold text-xs uppercase tracking-wider transition hover:text-slate-200">
                            Raw API Response
                        </button>
                    </div>
                    <span id="results-count" class="text-xs text-slate-500 font-medium">No results loaded</span>
                </div>

                <div id="loader" class="hidden flex-1 flex flex-col items-center justify-center gap-3">
                    <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
                    <p class="text-sm text-slate-400">Requesting provider API feed...</p>
                </div>

                <div id="placeholder-msg" class="flex-1 flex flex-col items-center justify-center gap-3 text-slate-500 text-center p-8">
                    <i class="fa-solid fa-plug-circle-exclamation text-4xl mb-2 text-slate-600"></i>
                    <p class="text-sm font-semibold">Select a reseller provider, verify configurations, and click "Get Raw Data & Preview" to start.</p>
                </div>

                <div id="preview-container" class="hidden flex-1 overflow-y-auto max-h-[700px] flex flex-col gap-3">
                    <div id="product-cards-grid" class="grid grid-cols-1 md:grid-cols-2 gap-3"></div>
                </div>

                <div id="raw-container" class="hidden flex-1 overflow-auto max-h-[700px] bg-slate-950 rounded-xl p-4 border border-white/5">
                    <pre id="raw-json-block" class="text-[11px] font-mono text-emerald-400 whitespace-pre-wrap"></pre>
                </div>
            </div>
        </section>
    </main>

    <script>
        const providers = {providers_json};
        let currentTab = 'preview';

        window.addEventListener('DOMContentLoaded', () => {{
            const select = document.getElementById('provider-select');
            providers.forEach(p => {{
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name;
                select.appendChild(opt);
            }});
        }});

        function onProviderChange() {{
            const select = document.getElementById('provider-select');
            const fields = document.getElementById('mapping-fields');
            const placeholder = document.getElementById('placeholder-msg');
            
            if (!select.value) {{
                fields.classList.add('hidden');
                placeholder.classList.remove('hidden');
                return;
            }}

            const provider = providers.find(p => p.id == select.value);
            if (provider) {{
                fields.classList.remove('hidden');
                
                document.getElementById('base_url').value = provider.base_url;
                document.getElementById('api_key').value = provider.api_key;
                document.getElementById('markup_percent').value = provider.markup_percent;
                document.getElementById('products_url').value = provider.products_url;
                document.getElementById('products_path').value = provider.products_path;
                document.getElementById('product_id_path').value = provider.product_id_path;
                document.getElementById('product_name_path').value = provider.product_name_path;
                document.getElementById('product_price_path').value = provider.product_price_path;
                document.getElementById('product_stock_path').value = provider.product_stock_path;
                
                document.getElementById('purchase_url').value = provider.purchase_url;
                document.getElementById('purchase_method').value = provider.purchase_method;
                document.getElementById('purchase_headers').value = provider.purchase_headers;
                document.getElementById('purchase_payload_template').value = provider.purchase_payload_template;
                document.getElementById('purchase_order_id_path').value = provider.purchase_order_id_path;
                document.getElementById('purchase_credentials_path').value = provider.purchase_credentials_path;
            }}
        }}

        function switchTab(tab) {{
            currentTab = tab;
            const tabPreview = document.getElementById('tab-preview');
            const tabRaw = document.getElementById('tab-raw');
            const containerPreview = document.getElementById('preview-container');
            const containerRaw = document.getElementById('raw-container');

            if (tab === 'preview') {{
                tabPreview.className = "px-4 py-2 border-b-2 border-indigo-500 text-indigo-400 font-semibold text-xs uppercase tracking-wider transition font-bold";
                tabRaw.className = "px-4 py-2 border-b-2 border-transparent text-slate-400 font-semibold text-xs uppercase tracking-wider transition hover:text-slate-200";
                
                if (document.getElementById('loader').classList.contains('hidden')) {{
                    containerPreview.classList.remove('hidden');
                    containerRaw.classList.add('hidden');
                }}
            }} else {{
                tabRaw.className = "px-4 py-2 border-b-2 border-indigo-500 text-indigo-400 font-semibold text-xs uppercase tracking-wider transition font-bold";
                tabPreview.className = "px-4 py-2 border-b-2 border-transparent text-slate-400 font-semibold text-xs uppercase tracking-wider transition hover:text-slate-200";
                
                if (document.getElementById('loader').classList.contains('hidden')) {{
                    containerRaw.classList.remove('hidden');
                    containerPreview.classList.add('hidden');
                }}
            }}
        }}

        async function getPreview() {{
            const select = document.getElementById('provider-select');
            if (!select.value) return;

            const loader = document.getElementById('loader');
            const placeholder = document.getElementById('placeholder-msg');
            const previewContainer = document.getElementById('preview-container');
            const rawContainer = document.getElementById('raw-container');
            const resultsCount = document.getElementById('results-count');

            placeholder.classList.add('hidden');
            previewContainer.classList.add('hidden');
            rawContainer.classList.add('hidden');
            loader.classList.remove('hidden');
            resultsCount.textContent = 'Loading...';

            const payload = {{
                provider_id: parseInt(select.value),
                base_url: document.getElementById('base_url').value,
                api_key: document.getElementById('api_key').value,
                markup_percent: parseFloat(document.getElementById('markup_percent').value || 0),
                products_url: document.getElementById('products_url').value,
                products_path: document.getElementById('products_path').value,
                product_id_path: document.getElementById('product_id_path').value,
                product_name_path: document.getElementById('product_name_path').value,
                product_price_path: document.getElementById('product_price_path').value,
                product_stock_path: document.getElementById('product_stock_path').value,
                purchase_headers: document.getElementById('purchase_headers').value
            }};

            try {{
                const resp = await fetch('/admin/preview_sync', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await resp.json();
                
                loader.classList.add('hidden');

                if (data.error) {{
                    resultsCount.innerHTML = `<span class="text-rose-400 font-bold"><i class="fa-solid fa-triangle-exclamation"></i> Error: ${{data.error}}</span>`;
                }} else {{
                    resultsCount.textContent = `${{data.mapped_products.length}} items mapped`;
                }}

                document.getElementById('raw-json-block').textContent = JSON.stringify(data.raw_response, null, 2);

                const grid = document.getElementById('product-cards-grid');
                grid.innerHTML = '';
                
                if (data.mapped_products && data.mapped_products.length > 0) {{
                    data.mapped_products.forEach(p => {{
                        const card = document.createElement('div');
                        card.className = "bg-slate-900/60 border border-white/5 rounded-xl p-4 flex flex-col justify-between gap-3 hover:border-indigo-500/20 transition";
                        card.innerHTML = `
                            <div class="flex flex-col gap-1">
                                <div class="flex items-center justify-between gap-2">
                                    <span class="text-[10px] font-bold font-mono text-slate-500 uppercase">ID: ${{p.id}}</span>
                                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${{p.stock > 0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}}">
                                        Stock: ${{p.stock}}
                                    </span>
                                </div>
                                <h4 class="text-xs font-bold text-slate-200 line-clamp-1">${{p.name}}</h4>
                            </div>
                            <div class="flex items-end justify-between pt-2 border-t border-white/5">
                                <div class="flex flex-col">
                                    <span class="text-[9px] text-slate-500 font-semibold uppercase">Original Price</span>
                                    <span class="text-xs font-mono text-slate-400">$${{p.original_price}}</span>
                                </div>
                                <div class="flex flex-col items-end">
                                    <span class="text-[9px] text-indigo-400 font-bold uppercase">Final Price (Markup)</span>
                                    <span class="text-sm font-extrabold text-white font-mono">$${{p.final_price}}</span>
                                </div>
                            </div>
                        `;
                        grid.appendChild(card);
                    }});
                }} else {{
                    grid.innerHTML = `
                        <div class="col-span-full py-8 text-center text-slate-500 text-xs">
                            No products mapped. Check that your path selectors match the API response structure.
                        </div>
                    `;
                }}

                if (currentTab === 'preview') {{
                    previewContainer.classList.remove('hidden');
                }} else {{
                    rawContainer.classList.remove('hidden');
                }}

            }} catch(err) {{
                loader.classList.add('hidden');
                resultsCount.textContent = 'Connection failed';
                alert('Connection error: ' + err.message);
            }}
        }}

        async function runSync() {{
            const select = document.getElementById('provider-select');
            if (!select.value) return;

            const confirmSync = confirm("This will save the current mappings to the database and run a full synchronization task to update your live Telegram shop catalog. Proceed?");
            if (!confirmSync) return;

            const payload = {{
                provider_id: parseInt(select.value),
                base_url: document.getElementById('base_url').value,
                api_key: document.getElementById('api_key').value,
                markup_percent: parseFloat(document.getElementById('markup_percent').value || 0),
                products_url: document.getElementById('products_url').value,
                products_path: document.getElementById('products_path').value,
                product_id_path: document.getElementById('product_id_path').value,
                product_name_path: document.getElementById('product_name_path').value,
                product_price_path: document.getElementById('product_price_path').value,
                product_stock_path: document.getElementById('product_stock_path').value,
                
                purchase_url: document.getElementById('purchase_url').value,
                purchase_method: document.getElementById('purchase_method').value,
                purchase_headers: document.getElementById('purchase_headers').value,
                purchase_payload_template: document.getElementById('purchase_payload_template').value,
                purchase_order_id_path: document.getElementById('purchase_order_id_path').value,
                purchase_credentials_path: document.getElementById('purchase_credentials_path').value
            }};

            try {{
                const resp = await fetch('/admin/run_sync', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await resp.json();
                
                if (data.success) {{
                    alert("Synchronization succeeded! Your local catalog has been successfully updated.");
                    window.location.href = '/admin/reseller-products/list';
                }} else {{
                    alert("Error running synchronization: " + (data.error || "Unknown error"));
                }}
            }} catch(err) {{
                alert("Request failed: " + err.message);
            }}
        }}
    </script>
</body>
</html>"""


# Health & Metrics Endpoints
async def health_check(request: Request) -> JSONResponse:
    health_status = {
        "status": "healthy",
        "checks": {},
    }

    try:
        async with Database().session() as s:
            await s.execute(text("SELECT 1"))
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        logger.error(f"Health check database error: {e}")
        health_status["checks"]["database"] = "error"
        health_status["status"] = "unhealthy"

    cache = get_cache_manager()
    if cache:
        health_status["checks"]["redis"] = "ok" if cache._healthy else "degraded"
    else:
        health_status["checks"]["redis"] = "not configured"

    metrics = get_metrics()
    if metrics:
        health_status["checks"]["metrics"] = "ok"
        health_status["uptime"] = metrics.get_metrics_summary()["uptime_seconds"]

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(health_status, status_code=status_code)


async def prometheus_metrics(request: Request) -> PlainTextResponse:
    if not request.session.get("authenticated"):
        return PlainTextResponse("Unauthorized", status_code=401)
    metrics = get_metrics()
    if not metrics:
        return PlainTextResponse("# Metrics not initialized\n", status_code=503)
    return PlainTextResponse(metrics.export_to_prometheus(), media_type="text/plain")


async def metrics_json(request: Request) -> JSONResponse:
    if not request.session.get("authenticated"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    metrics = get_metrics()
    if not metrics:
        return JSONResponse({"error": "Metrics not initialized"}, status_code=503)
    return JSONResponse(metrics.get_metrics_summary(), status_code=200)


class BroadcastAdmin(BaseView):
    name = "System Broadcast"
    icon = "fa-solid fa-bullhorn"

    @expose("/broadcast", methods=["GET"])
    async def broadcast_get(self, request: Request) -> HTMLResponse:
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/admin/login", status_code=303)

        if request.query_params.get("reset") == "1":
            request.app.state.broadcast_status = {
                "status": "idle",
                "total": 0,
                "sent": 0,
                "failed": 0,
                "start_time": None,
                "end_time": None,
            }

        status = request.app.state.broadcast_status
        error = request.query_params.get("error", "")
        success = request.query_params.get("success", "")

        error_message = ""
        if error == "message_empty":
            error_message = "Message text cannot be empty."
        elif error == "already_running":
            error_message = "A broadcast is already in progress."
        elif error == "no_users":
            error_message = "No active users found to broadcast to."
        elif error == "bot_not_ready":
            error_message = "Telegram Bot is not initialized or running."

        import json
        html = get_broadcast_html()
        html = html.replace("__STATUS_JSON__", json.dumps(status))
        html = html.replace("__ERROR_MESSAGE__", error_message)
        html = html.replace("__SUCCESS_MESSAGE__", success)

        return HTMLResponse(html)

    @expose("/broadcast", methods=["POST"])
    async def broadcast_post(self, request: Request) -> RedirectResponse:
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/admin/login", status_code=303)

        form = await request.form()
        message_text = form.get("message")
        if not message_text:
            return RedirectResponse(url="/admin/broadcast?error=message_empty", status_code=303)

        status = request.app.state.broadcast_status
        if status["status"] == "running":
            return RedirectResponse(url="/admin/broadcast?error=already_running", status_code=303)

        from sqlalchemy import select
        from bot.database.models.main import User

        async with Database().session() as s:
            result = await s.execute(select(User.telegram_id).where(User.is_blocked == False))
            user_ids = [row[0] for row in result.all()]

        if not user_ids:
            return RedirectResponse(url="/admin/broadcast?error=no_users", status_code=303)

        bot = getattr(request.app.state, "bot", None)
        if not bot:
            return RedirectResponse(url="/admin/broadcast?error=bot_not_ready", status_code=303)

        from bot.misc.services.broadcast_system import BroadcastManager
        manager = BroadcastManager(bot=bot)
        request.app.state.broadcast_manager = manager

        request.app.state.broadcast_status = {
            "status": "running",
            "total": len(user_ids),
            "sent": 0,
            "failed": 0,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
        }

        def progress_callback(stats):
            request.app.state.broadcast_status.update({
                "sent": stats.sent,
                "failed": stats.failed,
            })

        async def run_broadcast_task():
            try:
                stats = await manager.broadcast(user_ids, message_text, progress_callback=progress_callback)
                request.app.state.broadcast_status.update({
                    "status": "completed",
                    "sent": stats.sent,
                    "failed": stats.failed,
                    "end_time": datetime.now().isoformat(),
                })
                await log_audit("web_broadcast_completed", details=f"sent={stats.sent}, failed={stats.failed}")
            except Exception as e:
                logger.error(f"Web broadcast error: {e}")
                request.app.state.broadcast_status.update({
                    "status": "failed",
                    "error_message": str(e),
                    "end_time": datetime.now().isoformat(),
                })
                await log_audit("web_broadcast_failed", level="ERROR", details=str(e))

        bg_tasks = BackgroundTasks()
        bg_tasks.add_task(run_broadcast_task)

        await log_audit("web_broadcast_started", details=f"target_users={len(user_ids)}")

        response = RedirectResponse(url="/admin/broadcast", status_code=303)
        response.background = bg_tasks
        return response

    @expose("/broadcast/cancel", methods=["POST"])
    async def broadcast_cancel(self, request: Request) -> RedirectResponse:
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/admin/login", status_code=303)

        manager = getattr(request.app.state, "broadcast_manager", None)
        if manager:
            manager.cancel()
            request.app.state.broadcast_status.update({
                "status": "cancelled",
                "end_time": datetime.now().isoformat(),
            })
            await log_audit("web_broadcast_cancelled")

        return RedirectResponse(url="/admin/broadcast", status_code=303)

    @expose("/broadcast/status", methods=["GET"])
    async def broadcast_status_endpoint(self, request: Request) -> JSONResponse:
        if not request.session.get("authenticated"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return JSONResponse(request.app.state.broadcast_status)


def get_broadcast_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>System Broadcast - Admin Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body {
            font-family: 'Outfit', sans-serif;
            background-color: #0b0f19;
            background-image: radial-gradient(circle at top right, rgba(99, 102, 241, 0.12), transparent 400px),
                              radial-gradient(circle at bottom left, rgba(168, 85, 247, 0.12), transparent 400px);
        }
        .glass-card {
            background: rgba(17, 24, 39, 0.7);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }
    </style>
</head>
<body class="text-gray-100 min-h-screen flex flex-col items-center justify-start p-4 sm:p-8">
    <div class="w-full max-w-2xl">
        <!-- Navigation Header -->
        <div class="flex items-center justify-between mb-8">
            <div class="flex items-center gap-3">
                <div class="p-2.5 bg-indigo-600/20 rounded-xl border border-indigo-500/30">
                    <i class="fa-solid fa-bullhorn text-2xl text-indigo-400"></i>
                </div>
                <div>
                    <h1 class="text-2xl font-bold tracking-tight">System Broadcast</h1>
                    <p class="text-sm text-gray-400">Send mass messages to bot users</p>
                </div>
            </div>
            <a href="/admin/" class="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-300 bg-gray-800/80 hover:bg-gray-700/80 border border-gray-700/50 rounded-xl transition">
                <i class="fa-solid fa-arrow-left"></i> Back to Dashboard
            </a>
        </div>

        <!-- Notification Banner -->
        <div id="alert-box" class="hidden mb-6 p-4 rounded-xl border transition"></div>

        <!-- Main Card -->
        <div class="glass-card rounded-2xl p-6 sm:p-8">
            <!-- Form Section -->
            <div id="form-section">
                <form action="/admin/broadcast" method="POST" class="space-y-6">
                    <div>
                        <label for="message" class="block text-sm font-semibold text-gray-300 mb-2">Broadcast Message</label>
                        <textarea id="message" name="message" rows="8" class="w-full bg-gray-900/60 border border-gray-700/60 rounded-xl p-4 text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 transition resize-y" placeholder="Type your broadcast message here..."></textarea>
                    </div>

                    <!-- Styling helper tips -->
                    <div class="p-4 bg-gray-900/40 border border-gray-800 rounded-xl space-y-2">
                        <span class="text-xs font-semibold text-indigo-400 tracking-wider uppercase">💡 HTML Formatting Guide</span>
                        <div class="grid grid-cols-2 gap-2 text-xs text-gray-400">
                            <div><code>&lt;b&gt;bold text&lt;/b&gt;</code></div>
                            <div><code>&lt;i&gt;italic text&lt;/i&gt;</code></div>
                            <div><code>&lt;code&gt;code block&lt;/code&gt;</code></div>
                            <div><code>&lt;a href="url"&gt;link&lt;/a&gt;</code></div>
                        </div>
                    </div>

                    <button type="submit" class="w-full bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-medium py-3 px-4 rounded-xl shadow-lg hover:shadow-indigo-500/25 transition duration-200 flex items-center justify-center gap-2">
                        <i class="fa-solid fa-paper-plane"></i> Start Broadcast
                    </button>
                </form>
            </div>

            <!-- Status Section -->
            <div id="status-section" class="hidden space-y-6">
                <div class="flex items-center justify-between pb-4 border-b border-gray-800">
                    <div class="flex items-center gap-2">
                        <span class="relative flex h-3 w-3">
                            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                        </span>
                        <span class="font-semibold text-gray-200">Broadcasting...</span>
                    </div>
                    <span id="progress-percent" class="text-sm font-bold text-indigo-400">0%</span>
                </div>

                <!-- Progress Bar -->
                <div class="w-full bg-gray-900 rounded-full h-2.5 overflow-hidden">
                    <div id="progress-bar" class="bg-gradient-to-r from-indigo-500 to-purple-500 h-2.5 rounded-full transition-all duration-300" style="width: 0%"></div>
                </div>

                <!-- Stats Grid -->
                <div class="grid grid-cols-3 gap-4">
                    <div class="bg-gray-900/40 p-4 rounded-xl border border-gray-800/80 text-center">
                        <span class="block text-xs text-gray-400 mb-1">Total Users</span>
                        <span id="stat-total" class="text-lg font-bold text-gray-200">0</span>
                    </div>
                    <div class="bg-gray-900/40 p-4 rounded-xl border border-gray-800/80 text-center">
                        <span class="block text-xs text-gray-400 mb-1">Sent</span>
                        <span id="stat-sent" class="text-lg font-bold text-emerald-400">0</span>
                    </div>
                    <div class="bg-gray-900/40 p-4 rounded-xl border border-gray-800/80 text-center">
                        <span class="block text-xs text-gray-400 mb-1">Failed</span>
                        <span id="stat-failed" class="text-lg font-bold text-rose-400">0</span>
                    </div>
                </div>

                <!-- Cancel Form -->
                <form action="/admin/broadcast/cancel" method="POST">
                    <button type="submit" class="w-full bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/30 text-rose-400 font-medium py-3 px-4 rounded-xl transition duration-200 flex items-center justify-center gap-2">
                        <i class="fa-solid fa-circle-stop"></i> Cancel Broadcast
                    </button>
                </form>
            </div>

            <!-- Result Section -->
            <div id="result-section" class="hidden space-y-6">
                <div class="text-center space-y-2">
                    <div class="mx-auto w-12 h-12 bg-emerald-500/20 rounded-full flex items-center justify-center border border-emerald-500/30 mb-4">
                        <i id="result-icon" class="fa-solid fa-check text-xl text-emerald-400"></i>
                    </div>
                    <h3 id="result-title" class="text-xl font-bold">Broadcast Completed</h3>
                    <p id="result-subtitle" class="text-sm text-gray-400">All messages have been processed.</p>
                </div>

                <!-- Stats Summary -->
                <div class="bg-gray-900/40 p-5 rounded-xl border border-gray-800/80 space-y-3">
                    <div class="flex justify-between text-sm">
                        <span class="text-gray-400">Total Users:</span>
                        <span id="res-total" class="font-semibold text-gray-200">0</span>
                    </div>
                    <div class="flex justify-between text-sm">
                        <span class="text-gray-400">Successfully Sent:</span>
                        <span id="res-sent" class="font-semibold text-emerald-400">0</span>
                    </div>
                    <div class="flex justify-between text-sm">
                        <span class="text-gray-400">Failed / Blocked:</span>
                        <span id="res-failed" class="font-semibold text-rose-400">0</span>
                    </div>
                </div>

                <button onclick="resetBroadcastView()" class="w-full bg-gray-800 hover:bg-gray-700 text-gray-200 font-medium py-3 px-4 rounded-xl border border-gray-700 transition duration-200">
                    Create New Broadcast
                </button>
            </div>
        </div>
    </div>

    <script>
        const statusData = __STATUS_JSON__;
        const errorMessage = "__ERROR_MESSAGE__";
        const successMessage = "__SUCCESS_MESSAGE__";

        // Display notifications
        const alertBox = document.getElementById('alert-box');
        if (errorMessage) {
            alertBox.innerText = errorMessage;
            alertBox.className = "mb-6 p-4 rounded-xl border bg-rose-500/10 border-rose-500/30 text-rose-400 text-sm";
            alertBox.classList.remove('hidden');
        } else if (successMessage) {
            alertBox.innerText = successMessage;
            alertBox.className = "mb-6 p-4 rounded-xl border bg-emerald-500/10 border-emerald-500/30 text-emerald-400 text-sm";
            alertBox.classList.remove('hidden');
        }

        function showSection(sectionId) {
            ['form-section', 'status-section', 'result-section'].forEach(id => {
                const el = document.getElementById(id);
                if (id === sectionId) {
                    el.classList.remove('hidden');
                } else {
                    el.classList.add('hidden');
                }
            });
        }

        async function updateStatus() {
            try {
                const response = await fetch('/admin/broadcast/status');
                const data = await response.json();
                
                if (data.status === 'running') {
                    showSection('status-section');
                    
                    const total = data.total || 1;
                    const processed = data.sent + data.failed;
                    const percent = Math.round((processed / total) * 100);
                    
                    document.getElementById('progress-bar').style.width = percent + '%';
                    document.getElementById('progress-percent').innerText = percent + '%';
                    document.getElementById('stat-sent').innerText = data.sent;
                    document.getElementById('stat-failed').innerText = data.failed;
                    document.getElementById('stat-total').innerText = total;
                    
                    setTimeout(updateStatus, 1500);
                } else if (data.status === 'completed' || data.status === 'cancelled' || data.status === 'failed') {
                    showSection('result-section');
                    
                    // Update result details
                    document.getElementById('res-total').innerText = data.total;
                    document.getElementById('res-sent').innerText = data.sent;
                    document.getElementById('res-failed').innerText = data.failed;
                    
                    const title = document.getElementById('result-title');
                    const subtitle = document.getElementById('result-subtitle');
                    const icon = document.getElementById('result-icon');
                    const iconBg = icon.parentElement;
                    
                    if (data.status === 'completed') {
                        title.innerText = "Broadcast Completed";
                        subtitle.innerText = "All messages have been processed successfully.";
                        icon.className = "fa-solid fa-check text-xl text-emerald-400";
                        iconBg.className = "mx-auto w-12 h-12 bg-emerald-500/20 rounded-full flex items-center justify-center border border-emerald-500/30 mb-4";
                    } else if (data.status === 'cancelled') {
                        title.innerText = "Broadcast Cancelled";
                        subtitle.innerText = "Mailing was stopped manually.";
                        icon.className = "fa-solid fa-ban text-xl text-amber-400";
                        iconBg.className = "mx-auto w-12 h-12 bg-amber-500/20 rounded-full flex items-center justify-center border border-amber-500/30 mb-4";
                    } else {
                        title.innerText = "Broadcast Failed";
                        subtitle.innerText = data.error_message || "An error occurred during mailing.";
                        icon.className = "fa-solid fa-triangle-exclamation text-xl text-rose-400";
                        iconBg.className = "mx-auto w-12 h-12 bg-rose-500/20 rounded-full flex items-center justify-center border border-rose-500/30 mb-4";
                    }
                } else {
                    showSection('form-section');
                }
            } catch (e) {
                console.error("Error updating status:", e);
                setTimeout(updateStatus, 3000);
            }
        }

        function resetBroadcastView() {
            window.location.href = '/admin/broadcast?reset=1';
        }

        // Initialize state
        if (statusData.status === 'running') {
            updateStatus();
        } else if (statusData.status === 'completed' || statusData.status === 'cancelled' || statusData.status === 'failed') {
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.get('reset') === '1') {
                showSection('form-section');
            } else {
                showSection('result-section');
                document.getElementById('res-total').innerText = statusData.total;
                document.getElementById('res-sent').innerText = statusData.sent;
                document.getElementById('res-failed').innerText = statusData.failed;
                
                const title = document.getElementById('result-title');
                const subtitle = document.getElementById('result-subtitle');
                const icon = document.getElementById('result-icon');
                const iconBg = icon.parentElement;
                
                if (statusData.status === 'completed') {
                    title.innerText = "Broadcast Completed";
                    subtitle.innerText = "All messages have been processed successfully.";
                    icon.className = "fa-solid fa-check text-xl text-emerald-400";
                    iconBg.className = "mx-auto w-12 h-12 bg-emerald-500/20 rounded-full flex items-center justify-center border border-emerald-500/30 mb-4";
                } else if (statusData.status === 'cancelled') {
                    title.innerText = "Broadcast Cancelled";
                    subtitle.innerText = "Mailing was stopped manually.";
                    icon.className = "fa-solid fa-ban text-xl text-amber-400";
                    iconBg.className = "mx-auto w-12 h-12 bg-amber-500/20 rounded-full flex items-center justify-center border border-amber-500/30 mb-4";
                } else {
                    title.innerText = "Broadcast Failed";
                    subtitle.innerText = statusData.error_message || "An error occurred during mailing.";
                    icon.className = "fa-solid fa-triangle-exclamation text-xl text-rose-400";
                    iconBg.className = "mx-auto w-12 h-12 bg-rose-500/20 rounded-full flex items-center justify-center border border-rose-500/30 mb-4";
                }
            }
        } else {
            showSection('form-section');
        }
    </script>
</body>
</html>"""


# App Factory
def create_admin_app() -> Starlette:

    from bot.web.export import export_routes

    routes = [
        Route("/health", health_check),
        Route("/metrics", metrics_json),
        Route("/metrics/prometheus", prometheus_metrics),
    ] + export_routes

    app = Starlette(routes=routes)
    app.state.broadcast_status = {
        "status": "idle",
        "total": 0,
        "sent": 0,
        "failed": 0,
        "start_time": None,
        "end_time": None,
    }
    app.state.broadcast_manager = None
    app.add_middleware(SessionMiddleware, secret_key=EnvKeys.SECRET_KEY, max_age=1800)

    auth_backend = AdminAuth(secret_key=EnvKeys.SECRET_KEY)
    admin = Admin(
        app,
        engine=Database().engine,
        authentication_backend=auth_backend,
        title="Telegram Shop Admin",
    )

    admin.add_view(UserAdmin)
    admin.add_view(RoleAdmin)
    admin.add_view(GoodsAdmin)
    admin.add_view(ItemValuesAdmin)
    admin.add_view(BoughtGoodsAdmin)
    admin.add_view(OperationsAdmin)
    admin.add_view(PaymentsAdmin)
    admin.add_view(ReferralEarningsAdmin)
    admin.add_view(AuditLogAdmin)
    admin.add_view(PromoCodeAdmin)
    if EnvKeys.REVIEWS_ENABLED == "1":
        admin.add_view(ReviewsAdmin)
    admin.add_view(BroadcastAdmin)
    admin.add_view(ResellerProvidersAdmin)
    admin.add_view(ResellerProductsAdmin)
    admin.add_view(ResellerOrdersAdmin)
    admin.add_view(ResellerSyncAdmin)

    return app
