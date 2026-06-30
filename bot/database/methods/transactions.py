from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, update, exists as sa_exists, delete as sa_delete
from sqlalchemy.exc import IntegrityError

from bot.database.models import User, ItemValues, Goods, BoughtGoods, Payments, Operations
from bot.database.models.main import PromoCodes, PromoCodeUsages, ReferralEarnings
from bot.database import Database
from bot.misc import EnvKeys
from bot.database.methods.read import invalidate_user_cache, invalidate_stats_cache, invalidate_item_cache
from bot.database.methods.cache_utils import safe_create_task
from bot.database.methods.audit import log_audit


async def buy_item_transaction(telegram_id: int, item_name: str, promo_code: str = None, qty: int = 1) -> tuple[bool, str, dict | None]:
    """
    Purchase of multiple items atomically.
    Validates user, product, balance, availability, and promo codes.
    Returns: (Success, Message/Error code, Purchase data)
    """
    max_retries = 3
    for attempt in range(max_retries):
        async with Database().session() as s:
            try:
                # 1. Lock the user to check the balance
                user = (await s.execute(
                    select(User).where(User.telegram_id == telegram_id).with_for_update()
                )).scalars().one_or_none()

                if not user:
                    await s.rollback()
                    return False, "user_not_found", None

                # 2. Get information about the product
                goods = (await s.execute(
                    select(Goods).where(Goods.name == item_name).with_for_update()
                )).scalars().one_or_none()

                if not goods:
                    await s.rollback()
                    return False, "item_not_found", None

                price = Decimal(str(goods.price))
                final_price = price
                discount_info = None

                # 2.5. Apply promo code if provided
                if promo_code:
                    promo = (await s.execute(
                        select(PromoCodes).where(PromoCodes.code == promo_code.upper()).with_for_update()
                    )).scalars().first()

                    if not promo or not promo.is_active:
                        await s.rollback()
                        return False, "promo_invalid", None

                    if promo.discount_type == "balance":
                        await s.rollback()
                        return False, "promo_invalid", None

                    if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
                        await s.rollback()
                        return False, "promo_expired", None

                    if promo.max_uses > 0 and promo.current_uses >= promo.max_uses:
                        await s.rollback()
                        return False, "promo_max_uses", None

                    # Check per-user usage
                    used = (await s.execute(
                        select(sa_exists().where(
                            PromoCodeUsages.promo_id == promo.id,
                            PromoCodeUsages.user_id == telegram_id
                        ))
                    )).scalar()
                    if used:
                        await s.rollback()
                        return False, "promo_already_used", None

                    if promo.item_id and promo.item_id != goods.id:
                        await s.rollback()
                        return False, "promo_wrong_item", None

                    # Promo logic
                    final_price = price
                    if promo.discount_type == 'percent':
                        final_price -= price * (Decimal(str(promo.discount_value)) / Decimal(100))
                    elif promo.discount_type == 'fixed':
                        final_price -= Decimal(str(promo.discount_value))
                        if final_price < 0:
                            final_price = Decimal("0")
                    final_price = final_price.quantize(Decimal("0.01"))

                    # Record usage
                    promo.current_uses += 1
                    s.add(PromoCodeUsages(promo_id=promo.id, user_id=telegram_id))
                    discount_info = {
                        "code": promo.code,
                        "original_price": float(price),
                        "discount": float(price - final_price),
                    }
                    
                total_price = final_price * qty

                # 3. Checking the balance
                if user.balance < total_price:
                    await s.rollback()
                    return False, "insufficient_funds", None

                # 4. Receive and lock the goods for purchase (blocking wait for row lock)
                item_values_result = (await s.execute(
                    select(ItemValues).where(ItemValues.item_id == goods.id).with_for_update().limit(qty)
                )).scalars().all()

                if not item_values_result:
                    await s.rollback()
                    return False, "out_of_stock", None

                is_infinity = False
                values_str = []
                if len(item_values_result) > 0 and item_values_result[0].is_infinity:
                    is_infinity = True
                    values_str = [item_values_result[0].value] * qty
                else:
                    if len(item_values_result) < qty:
                        await s.rollback()
                        return False, "out_of_stock", None
                    values_str = [iv.value for iv in item_values_result]
                    # 5. If the product is not endless, we remove it
                    for iv in item_values_result:
                        await s.delete(iv)

                # 6. Write off the balance
                user.balance -= total_price

                # 7. Create a purchase record
                bought_items = []
                unique_ids = []
                for val in values_str:
                    uid = uuid4().int >> 65
                    bought_item = BoughtGoods(
                        name=item_name,
                        value=val,
                        price=final_price,
                        buyer_id=telegram_id,
                        bought_datetime=datetime.now(timezone.utc),
                        unique_id=uid
                    )
                    s.add(bought_item)
                    bought_items.append(bought_item)
                    unique_ids.append(uid)
                    
                await s.flush()

                # 8. Commit the transaction
                await s.commit()

                safe_create_task(invalidate_user_cache(telegram_id))
                safe_create_task(invalidate_stats_cache())
                safe_create_task(invalidate_item_cache(item_name))

                result_data = {
                    "item_name": item_name,
                    "value": "\n".join(values_str),
                    "price": float(total_price),
                    "new_balance": float(user.balance),
                    "unique_id": unique_ids[0] if unique_ids else 0,
                    "bought_id": bought_items[0].id if bought_items else 0,
                    "bought_datetime": bought_items[0].bought_datetime.isoformat() if bought_items else "",
                }
                if discount_info:
                    result_data["discount"] = discount_info

                return True, "success", result_data

            except IntegrityError as e:
                await s.rollback()
                if "unique_id" in str(e).lower() and attempt < max_retries - 1:
                    continue  # Retry with a new unique_id
                await log_audit(
                    "purchase_failed",
                    level="WARNING",
                    user_id=telegram_id,
                    resource_type="Item",
                    resource_id=item_name,
                    details=str(e),
                )
                return False, "transaction_error", None

            except Exception as e:
                await s.rollback()
                await log_audit(
                    "purchase_failed",
                    level="WARNING",
                    user_id=telegram_id,
                    resource_type="Item",
                    resource_id=item_name,
                    details=str(e),
                )
                return False, "transaction_error", None

    return False, "transaction_error", None


async def process_payment_with_referral(
        user_id: int,
        amount: Decimal,
        provider: str,
        external_id: str,
        referral_percent: int = 0
) -> tuple[bool, str]:
    """
    Processing a payment with a referral bonus in one transaction.
    Returns (success, message)
    """

    async with Database().session() as s:
        try:
            # 1. Check the idempotency of the payment
            existing_payment = (await s.execute(
                select(Payments).where(
                    Payments.provider == provider,
                    Payments.external_id == external_id
                ).with_for_update()
            )).scalars().first()

            if existing_payment:
                if existing_payment.status == "succeeded":
                    await s.rollback()
                    return False, "already_processed"
                existing_payment.status = "succeeded"
            else:
                payment = Payments(
                    provider=provider,
                    external_id=external_id,
                    user_id=user_id,
                    amount=amount,
                    currency=EnvKeys.PAY_CURRENCY,
                    status="succeeded"
                )
                s.add(payment)

            # 2. Update the user's balance
            user = (await s.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )).scalars().one()

            user.balance += amount

            # 3. Create a transaction record
            operation = Operations(
                user_id=user_id,
                operation_value=amount,
                operation_time=datetime.now(timezone.utc)
            )
            s.add(operation)

            # 4. Process the referral bonus
            clamped_percent = min(max(referral_percent, 0), 99)
            if clamped_percent > 0 and user.referral_id and user.referral_id != user_id:
                referral_amount = (Decimal(clamped_percent) / Decimal(100)) * amount

                if referral_amount > 0:
                    referrer = (await s.execute(
                        select(User).where(User.telegram_id == user.referral_id).with_for_update()
                    )).scalars().one_or_none()

                    if referrer:
                        referrer.balance += referral_amount
                        await log_audit(
                            "referral_bonus",
                            user_id=user.referral_id,
                            resource_type="User",
                            resource_id=str(user_id),
                            details=f"paid={amount}, bonus={referral_amount}",
                        )

                        earning = ReferralEarnings(
                            referrer_id=user.referral_id,
                            referral_id=user_id,
                            amount=referral_amount,
                            original_amount=amount
                        )
                        s.add(earning)

            referrer_id = user.referral_id if clamped_percent > 0 else None

            await s.commit()

            safe_create_task(invalidate_user_cache(user_id))
            safe_create_task(invalidate_stats_cache())
            if referrer_id:
                safe_create_task(invalidate_user_cache(referrer_id))

            return True, "success"

        except IntegrityError:
            await s.rollback()
            return False, "already_processed"

        except Exception as e:
            await s.rollback()
            await log_audit(
                "payment_failed",
                level="WARNING",
                user_id=user_id,
                resource_type="Payment",
                details=f"provider={provider}, amount={amount}, error={e}",
            )
            return False, "payment_error"





async def admin_balance_change(telegram_id: int, amount: Decimal) -> tuple[bool, str]:
    """
    Atomic admin balance change (top-up or deduction) with operation record.
    amount > 0 for top-up, amount < 0 for deduction.
    Returns (success, message).
    """
    async with Database().session() as s:
        try:
            user = (await s.execute(
                select(User).where(User.telegram_id == telegram_id).with_for_update()
            )).scalars().one_or_none()

            if not user:
                await s.rollback()
                return False, "user_not_found"

            if amount < 0 and user.balance < abs(amount):
                await s.rollback()
                return False, "insufficient_funds"

            user.balance += amount

            operation = Operations(
                user_id=telegram_id,
                operation_value=amount,
                operation_time=datetime.now(timezone.utc)
            )
            s.add(operation)

            await s.commit()

            safe_create_task(invalidate_user_cache(telegram_id))
            safe_create_task(invalidate_stats_cache())

            return True, "success"

        except Exception as e:
            await s.rollback()
            await log_audit(
                "admin_balance_change_failed",
                level="WARNING",
                user_id=telegram_id,
                resource_type="User",
                details=f"amount={amount}, error={e}",
            )
            return False, "balance_change_error"


async def redeem_balance_promo(code: str, user_id: int) -> tuple[bool, str, Decimal | None]:
    """
    Redeem a balance-type promo code: add discount_value to user balance.
    Returns (success, error_key_or_empty, amount_added).
    """
    async with Database().session() as s:
        try:
            user = (await s.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )).scalars().one_or_none()
            if not user:
                await s.rollback()
                return False, "promo.not_found", None

            promo = (await s.execute(
                select(PromoCodes).where(PromoCodes.code == code.upper()).with_for_update()
            )).scalars().first()

            if not promo:
                await s.rollback()
                return False, "promo.not_found", None
            if not promo.is_active:
                await s.rollback()
                return False, "promo.inactive", None
            if promo.discount_type != "balance":
                await s.rollback()
                return False, "promo.not_balance_type", None
            if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
                await s.rollback()
                return False, "promo.expired", None
            if promo.max_uses > 0 and promo.current_uses >= promo.max_uses:
                await s.rollback()
                return False, "promo.max_uses_reached", None

            used = (await s.execute(
                select(sa_exists().where(
                    PromoCodeUsages.promo_id == promo.id,
                    PromoCodeUsages.user_id == user_id
                ))
            )).scalar()
            if used:
                await s.rollback()
                return False, "promo.already_used", None

            amount = Decimal(str(promo.discount_value))
            user.balance += amount
            promo.current_uses += 1
            s.add(PromoCodeUsages(promo_id=promo.id, user_id=user_id))
            s.add(Operations(
                user_id=user_id,
                operation_value=amount,
                operation_time=datetime.now(timezone.utc),
            ))

            await s.commit()
            safe_create_task(invalidate_user_cache(user_id))
            safe_create_task(invalidate_stats_cache())
            return True, "", amount

        except Exception as e:
            await s.rollback()
            await log_audit(
                "promo_redeem_failed",
                level="WARNING",
                user_id=user_id,
                resource_type="PromoCode",
                resource_id=code,
                details=str(e),
            )
            return False, "errors.something_wrong", None
