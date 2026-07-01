import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import select, delete
from bot.database.main import Database
from bot.database.models.main import BoughtGoods, User, ResellerProviders, ResellerProducts, ResellerOrders, Operations, Goods
from bot.database.methods.audit import log_audit
from bot.misc.services.reseller.sync import get_provider_client

logger = logging.getLogger(__name__)

async def dispatch_reseller_purchase(telegram_id: int, bought_id: int, item_name: str, final_price: Decimal, qty: int = 1) -> tuple[bool, str]:
    """
    Dispatches a purchase to the upstream reseller API.
    If success: updates BoughtGoods with the retrieved credentials and logs ResellerOrders as success.
    If failure: refunds the user's balance, deletes the BoughtGoods record, and logs ResellerOrders as failed.
    """
    async with Database().session() as session:
        # Check if there is a reseller mapping for this product
        stmt = select(ResellerProducts).join(Goods, ResellerProducts.mapped_goods_id == Goods.id).where(Goods.name == item_name)
        res_prods = (await session.execute(stmt)).scalars().all()

        if not res_prods:
            return True, "not_resold"

        # Find the cheapest in-stock active provider
        selected_rp = None
        selected_provider = None
        for rp in res_prods:
            stmt = select(ResellerProviders).where(ResellerProviders.id == rp.provider_id, ResellerProviders.is_active == True)
            provider = (await session.execute(stmt)).scalars().first()
            if not provider:
                continue

            if rp.stock >= qty:
                if selected_rp is None or rp.original_price < selected_rp.original_price:
                    selected_rp = rp
                    selected_provider = provider

        # Fallback to any provider if none had explicit stock > 0
        if not selected_rp:
            for rp in res_prods:
                stmt = select(ResellerProviders).where(ResellerProviders.id == rp.provider_id, ResellerProviders.is_active == True)
                provider = (await session.execute(stmt)).scalars().first()
                if provider:
                    selected_rp = rp
                    selected_provider = provider
                    break

        if not selected_rp or not selected_provider:
            await refund_and_cleanup(session, telegram_id, bought_id, final_price, "No active reseller provider with stock found.")
            return False, "reseller_out_of_stock"

        # Generate idempotency key
        idempotency_key = f"mk_{uuid.uuid4().hex[:8]}_{uuid.uuid4().hex[:24]}"

        # Create ResellerOrders entry in pending state
        reseller_order = ResellerOrders(
            bought_goods_id=bought_id,
            provider_id=selected_provider.id,
            upstream_product_id=selected_rp.upstream_id,
            idempotency_key=idempotency_key,
            status="pending"
        )
        session.add(reseller_order)
        await session.commit()

        # Call reseller API
        client = get_provider_client(selected_provider)
        try:
            success, upstream_order_id, credentials = await client.purchase_product(
                upstream_id=selected_rp.upstream_id,
                quantity=qty,
                idempotency_key=idempotency_key
            )
        except Exception as e:
            logger.error(f"Upstream reseller purchase exception: {e}")
            success, upstream_order_id, credentials = False, None, str(e)
        finally:
            await client.close()

        # Re-open session to process outcome
        async with Database().session() as s2:
            db_order = (await s2.execute(select(ResellerOrders).where(ResellerOrders.idempotency_key == idempotency_key))).scalars().one()
            
            if success and credentials:
                # Update BoughtGoods value with credentials
                bg = (await s2.execute(select(BoughtGoods).where(BoughtGoods.id == bought_id))).scalars().one_or_none()
                if bg:
                    bg.value = credentials

                db_order.status = "success"
                db_order.upstream_order_id = upstream_order_id
                
                # Decrement upstream stock in local cache/db
                rp_db = (await s2.execute(select(ResellerProducts).where(ResellerProducts.id == selected_rp.id))).scalars().one_or_none()
                if rp_db and rp_db.stock >= qty:
                    rp_db.stock -= qty

                await s2.commit()
                await log_audit("reseller_purchase_success", user_id=telegram_id, details=f"item={item_name}, order={upstream_order_id}")
                return True, credentials
            else:
                db_order.status = "failed"
                db_order.error_message = credentials or "Unknown provider error"
                await s2.commit()

                await refund_and_cleanup(s2, telegram_id, bought_id, final_price, credentials)
                return False, "reseller_purchase_failed"


async def refund_and_cleanup(session, telegram_id: int, bought_id: int, final_price: Decimal, error_details: str):
    # Refund user balance
    user = (await session.execute(select(User).where(User.telegram_id == telegram_id).with_for_update())).scalars().one_or_none()
    if user:
        user.balance += final_price
        
        # Log refund operation
        refund_op = Operations(
            user_id=telegram_id,
            operation_value=final_price,
            operation_time=datetime.now(timezone.utc)
        )
        session.add(refund_op)

    # Delete the placeholder BoughtGoods record
    bg = (await session.execute(select(BoughtGoods).where(BoughtGoods.id == bought_id))).scalars().one_or_none()
    if bg:
        await session.delete(bg)

    await session.commit()
    await log_audit(
        "reseller_purchase_refunded",
        level="ERROR",
        user_id=telegram_id,
        details=f"bought_id={bought_id}, refund_amount={final_price}, reason={error_details}"
    )
