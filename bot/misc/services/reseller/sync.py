import logging
from decimal import Decimal
from difflib import SequenceMatcher
from sqlalchemy import select, delete
from bot.database.main import Database
from bot.database.models.main import Goods, ItemValues, ResellerProviders, ResellerProducts
from bot.misc.services.reseller.client import (
    CatalystClient, AIMarketClient, MMOStoreClient, CanbosoClient, BunaiStoreClient
)

logger = logging.getLogger(__name__)

def get_provider_client(provider: ResellerProviders):
    name_lower = provider.name.lower()
    if "catalyst" in name_lower:
        return CatalystClient(provider.base_url, provider.api_key)
    elif "ai" in name_lower or "market" in name_lower:
        return AIMarketClient(provider.base_url, provider.api_key)
    elif "mmo" in name_lower:
        return MMOStoreClient(provider.base_url, provider.api_key)
    elif "canboso" in name_lower:
        return CanbosoClient(provider.base_url, provider.api_key)
    else:
        return BunaiStoreClient(provider.base_url, provider.api_key)


def compute_similarity(name1: str, name2: str) -> float:
    n1 = "".join([c for c in name1.lower() if c.isalnum() or c.isspace()]).strip()
    n2 = "".join([c for c in name2.lower() if c.isalnum() or c.isspace()]).strip()
    return SequenceMatcher(None, n1, n2).ratio()


async def sync_reseller_products():
    """
    1. Fetch products from all active reseller API providers.
    2. Upsert them in reseller_products.
    3. Perform similarity matching/deduplication to link with local unified Goods.
    4. Set lowest mapped in-stock price (with markup) as local Goods price.
    5. Maintain ItemValues placeholders for stock indicators.
    """
    async with Database().session() as session:
        # Fetch active providers
        res = await session.execute(select(ResellerProviders).where(ResellerProviders.is_active == True))
        providers = res.scalars().all()

        all_upstream_items = []
        for provider in providers:
            client = get_provider_client(provider)
            try:
                products = await client.get_products()
                for p in products:
                    all_upstream_items.append((provider, p))
            except Exception as e:
                logger.error(f"Error fetching products for provider {provider.name}: {e}")
            finally:
                await client.close()

        # Step 2: Update reseller_products records
        for provider, p in all_upstream_items:
            stmt = select(ResellerProducts).where(
                ResellerProducts.provider_id == provider.id,
                ResellerProducts.upstream_id == p["id"]
            )
            res_prod = (await session.execute(stmt)).scalars().first()
            if not res_prod:
                res_prod = ResellerProducts(
                    provider_id=provider.id,
                    upstream_id=p["id"],
                    name=p["name"],
                    original_price=p["price"],
                    stock=p["stock"]
                )
                session.add(res_prod)
            else:
                res_prod.name = p["name"]
                res_prod.original_price = p["price"]
                res_prod.stock = p["stock"]
        
        await session.commit()

        # Step 3: Similarity deduplication and mapping to unified local Goods
        stmt = select(ResellerProducts)
        res_prods = (await session.execute(stmt)).scalars().all()

        stmt = select(Goods)
        all_goods = (await session.execute(stmt)).scalars().all()

        for rp in res_prods:
            if rp.mapped_goods_id:
                continue

            best_match = None
            best_score = 0.0
            for g in all_goods:
                score = compute_similarity(rp.name, g.name)
                if score > best_score:
                    best_score = score
                    best_match = g

            if best_score > 0.85 and best_match:
                rp.mapped_goods_id = best_match.id
                logger.info(f"Mapped upstream product '{rp.name}' to existing Goods '{best_match.name}' (score: {best_score:.2f})")
            else:
                stmt = select(ResellerProviders.markup_percent).where(ResellerProviders.id == rp.provider_id)
                markup_pct = (await session.execute(stmt)).scalar() or Decimal("0.00")
                markup = Decimal(1.0) + (markup_pct / Decimal(100.0))
                
                new_price = rp.original_price * markup
                new_goods = Goods(
                    name=rp.name,
                    price=new_price,
                    description=f"Auto-generated resold product from API."
                )
                session.add(new_goods)
                await session.flush()
                rp.mapped_goods_id = new_goods.id
                all_goods.append(new_goods)
                logger.info(f"Created new unified Goods '{new_goods.name}' for upstream product '{rp.name}'")

        await session.commit()

        # Step 4 & 5: Update prices and stock placeholders
        stmt = select(Goods)
        all_goods = (await session.execute(stmt)).scalars().all()

        for g in all_goods:
            stmt = select(ResellerProducts).where(ResellerProducts.mapped_goods_id == g.id)
            mapped_prods = (await session.execute(stmt)).scalars().all()

            if not mapped_prods:
                continue

            cheapest_price = None
            total_stock = 0
            for mp in mapped_prods:
                stmt = select(ResellerProviders).where(ResellerProviders.id == mp.provider_id)
                provider = (await session.execute(stmt)).scalars().first()
                if not provider or not provider.is_active:
                    continue

                markup = Decimal(1.0) + (provider.markup_percent / Decimal(100.0))
                price_with_markup = mp.original_price * markup
                total_stock += mp.stock

                if mp.stock > 0:
                    if cheapest_price is None or price_with_markup < cheapest_price:
                        cheapest_price = price_with_markup

            if cheapest_price is None:
                for mp in mapped_prods:
                    stmt = select(ResellerProviders).where(ResellerProviders.id == mp.provider_id)
                    provider = (await session.execute(stmt)).scalars().first()
                    if not provider or not provider.is_active:
                        continue
                    markup = Decimal(1.0) + (provider.markup_percent / Decimal(100.0))
                    price_with_markup = mp.original_price * markup
                    if cheapest_price is None or price_with_markup < cheapest_price:
                        cheapest_price = price_with_markup

            if cheapest_price is not None:
                g.price = cheapest_price

            await session.execute(delete(ItemValues).where(ItemValues.item_id == g.id, ItemValues.value == "API_RESELL"))
            
            if total_stock > 0:
                placeholder = ItemValues(item_id=g.id, value="API_RESELL", is_infinity=True)
                session.add(placeholder)

        await session.commit()
