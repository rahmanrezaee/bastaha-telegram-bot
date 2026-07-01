import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import Goods, ItemValues, BoughtGoods, User, ResellerProviders, ResellerProducts, ResellerOrders
from bot.misc.services.reseller.sync import sync_reseller_products, compute_similarity
from bot.misc.services.reseller.purchase import dispatch_reseller_purchase


def test_similarity():
    assert compute_similarity("Telegram Premium 1 Month", "Telegram Premium - 1 Month") > 0.85
    assert compute_similarity("Netflix 1 Screen", "Spotify Premium") < 0.50


@pytest.mark.asyncio
async def test_reseller_product_sync():
    # 1. Seed provider in database
    async with Database().session() as session:
        provider = ResellerProviders(
            name="Catalyst Store Test",
            base_url="https://api.catalyststore.online",
            api_key="sk_live_test",
            is_active=True,
            markup_percent=Decimal("10.00")
        )
        session.add(provider)
        await session.commit()
        provider_id = provider.id

    # 2. Mock the Client response
    mock_products = [
        {"id": "cat_prod_1", "name": "Telegram Premium 1 Month", "price": Decimal("100.00"), "stock": 10},
        {"id": "cat_prod_2", "name": "Netflix 1 Month", "price": Decimal("200.00"), "stock": 0}
    ]

    mock_client = MagicMock()
    mock_client.get_products = AsyncMock(return_value=mock_products)
    mock_client.close = AsyncMock()

    with patch("bot.misc.services.reseller.sync.get_provider_client", return_value=mock_client):
        await sync_reseller_products()

    # 3. Assert database records were created
    async with Database().session() as session:
        # Check ResellerProducts
        res_prods = (await session.execute(select(ResellerProducts))).scalars().all()
        assert len(res_prods) == 2
        assert res_prods[0].upstream_id == "cat_prod_1"
        assert res_prods[0].stock == 10
        assert res_prods[0].original_price == Decimal("100.00")

        # Check corresponding Goods
        goods = (await session.execute(select(Goods))).scalars().all()
        assert len(goods) == 2
        # Verify 10% markup was applied
        assert goods[0].price == Decimal("110.00")
        assert goods[1].price == Decimal("220.00")

        # Verify stock placeholder was set for the one with stock > 0
        ivs = (await session.execute(select(ItemValues).where(ItemValues.item_id == goods[0].id))).scalars().all()
        assert len(ivs) == 1
        assert ivs[0].value == "API_RESELL"
        assert ivs[0].is_infinity is True

        # Verify no stock placeholder was set for the one with stock == 0
        ivs_out = (await session.execute(select(ItemValues).where(ItemValues.item_id == goods[1].id))).scalars().all()
        assert len(ivs_out) == 0


@pytest.mark.asyncio
async def test_dispatch_reseller_purchase_success(user_factory, item_factory):
    # Setup user
    await user_factory(telegram_id=500001, balance=500)
    
    # Setup provider and mapped product
    async with Database().session() as session:
        provider = ResellerProviders(
            name="Catalyst Store Test",
            base_url="https://api.catalyststore.online",
            api_key="sk_live_test",
            is_active=True,
            markup_percent=Decimal("10.00")
        )
        session.add(provider)
        await session.commit()
        provider_id = provider.id

    # Create local Goods and ResellerProduct mapping
    await item_factory(name="Telegram Premium 1 Month", price=110)
    async with Database().session() as session:
        local_goods = (await session.execute(select(Goods).where(Goods.name == "Telegram Premium 1 Month"))).scalars().one()
        local_goods_id = local_goods.id
    
    async with Database().session() as session:
        res_prod = ResellerProducts(
            provider_id=provider_id,
            upstream_id="cat_prod_1",
            name="Telegram Premium 1 Month",
            original_price=Decimal("100.00"),
            stock=10,
            mapped_goods_id=local_goods_id
        )
        session.add(res_prod)
        
        # Create a placeholder BoughtGoods
        bg = BoughtGoods(
            name="Telegram Premium 1 Month",
            value="API_RESELL",
            price=Decimal("110.00"),
            buyer_id=500001,
            unique_id=12345
        )
        session.add(bg)
        await session.commit()
        bought_id = bg.id

    # Mock Client purchase method to return success credentials
    mock_client = MagicMock()
    mock_client.purchase_product = AsyncMock(return_value=(True, "order_123", "RET-CRED-ABC-123"))
    mock_client.close = AsyncMock()

    with patch("bot.misc.services.reseller.purchase.get_provider_client", return_value=mock_client):
        success, credentials = await dispatch_reseller_purchase(
            telegram_id=500001,
            bought_id=bought_id,
            item_name="Telegram Premium 1 Month",
            final_price=Decimal("110.00"),
            qty=1
        )

    assert success is True
    assert credentials == "RET-CRED-ABC-123"

    # Verify BoughtGoods was updated and ResellerOrders has success
    async with Database().session() as session:
        bg_db = (await session.execute(select(BoughtGoods).where(BoughtGoods.id == bought_id))).scalars().first()
        assert bg_db.value == "RET-CRED-ABC-123"

        order_db = (await session.execute(select(ResellerOrders).where(ResellerOrders.bought_goods_id == bought_id))).scalars().first()
        assert order_db.status == "success"
        assert order_db.upstream_order_id == "order_123"


@pytest.mark.asyncio
async def test_dispatch_reseller_purchase_failure(user_factory, item_factory):
    # Setup user
    await user_factory(telegram_id=500002, balance=500)
    
    # Setup provider and mapped product
    async with Database().session() as session:
        provider = ResellerProviders(
            name="Catalyst Store Test",
            base_url="https://api.catalyststore.online",
            api_key="sk_live_test",
            is_active=True,
            markup_percent=Decimal("10.00")
        )
        session.add(provider)
        await session.commit()
        provider_id = provider.id

    # Create local Goods and ResellerProduct mapping
    await item_factory(name="Telegram Premium 1 Month", price=110)
    async with Database().session() as session:
        local_goods = (await session.execute(select(Goods).where(Goods.name == "Telegram Premium 1 Month"))).scalars().one()
        local_goods_id = local_goods.id
    
    async with Database().session() as session:
        res_prod = ResellerProducts(
            provider_id=provider_id,
            upstream_id="cat_prod_1",
            name="Telegram Premium 1 Month",
            original_price=Decimal("100.00"),
            stock=10,
            mapped_goods_id=local_goods_id
        )
        session.add(res_prod)
        
        # Create a placeholder BoughtGoods
        bg = BoughtGoods(
            name="Telegram Premium 1 Month",
            value="API_RESELL",
            price=Decimal("110.00"),
            buyer_id=500002,
            unique_id=54321
        )
        session.add(bg)
        await session.commit()
        bought_id = bg.id

    # Mock Client purchase method to return failure
    mock_client = MagicMock()
    mock_client.purchase_product = AsyncMock(return_value=(False, None, "Out of Stock in Upstream"))
    mock_client.close = AsyncMock()

    # Subtract the price to simulate balance decrease during buy_item_transaction
    async with Database().session() as session:
        user = (await session.execute(select(User).where(User.telegram_id == 500002))).scalars().first()
        user.balance -= Decimal("110.00")
        await session.commit()

    with patch("bot.misc.services.reseller.purchase.get_provider_client", return_value=mock_client):
        success, credentials = await dispatch_reseller_purchase(
            telegram_id=500002,
            bought_id=bought_id,
            item_name="Telegram Premium 1 Month",
            final_price=Decimal("110.00"),
            qty=1
        )

    assert success is False
    assert credentials == "reseller_purchase_failed"

    # Verify user was refunded and BoughtGoods record was cleaned up
    async with Database().session() as session:
        user = (await session.execute(select(User).where(User.telegram_id == 500002))).scalars().first()
        assert user.balance == Decimal("500.00")

        bg_db = (await session.execute(select(BoughtGoods).where(BoughtGoods.id == bought_id))).scalars().first()
        assert bg_db is None

        order_db = (await session.execute(select(ResellerOrders))).scalars().first()
        assert order_db.status == "failed"
        assert order_db.error_message == "Out of Stock in Upstream"
