from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, exists

from bot.database.models import User, ItemValues, Goods, Operations, Payments, ReferralEarnings, Role
from bot.database.models.main import PromoCodes, Reviews
from bot.database import Database
from bot.database.methods.cache_utils import safe_create_task
from bot.database.methods.read import invalidate_stats_cache


async def create_user(telegram_id: int, registration_date: datetime, referral_id: int | None, role: int = 1) -> None:
    """Create user if missing; commit."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(User.telegram_id == telegram_id)))
        if result.scalar():
            return
        s.add(
            User(
                telegram_id=telegram_id,
                role_id=role,
                registration_date=registration_date,
                referral_id=referral_id,
            )
        )


async def create_item(item_name: str, item_description: str, item_price: int) -> None:
    """Insert item (goods); commit."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(Goods.name == item_name)))
        if result.scalar():
            return
        s.add(
            Goods(
                name=item_name,
                description=item_description,
                price=item_price,
            )
        )

    safe_create_task(invalidate_stats_cache())


async def add_values_to_item(item_name: str, value: str, is_infinity: bool) -> bool:
    """Add item value if not duplicate; True if inserted. Resolves item_name to item_id."""
    value_norm = (value or "").strip()
    if not value_norm:
        return False

    async with Database().session() as s:
        item_id = (await s.execute(select(Goods.id).where(Goods.name == item_name))).scalar()
        if not item_id:
            return False

        dup = (await s.execute(
            select(exists().where(
                ItemValues.item_id == item_id,
                ItemValues.value == value_norm,
            ))
        )).scalar()
        if dup:
            return False

        try:
            s.add(ItemValues(item_id=item_id, value=value_norm, is_infinity=bool(is_infinity)))
            await s.flush()
            from bot.database.methods.read import invalidate_item_cache
            from bot.database.methods.cache_utils import safe_create_task
            safe_create_task(invalidate_item_cache(item_name))
            return True
        except Exception:
            return False





async def create_operation(user_id: int, value: int, operation_time: datetime) -> None:
    """Record completed balance operation; commit."""
    async with Database().session() as s:
        s.add(Operations(user_id, value, operation_time))


async def create_pending_payment(provider: str, external_id: str, user_id: int, amount: int, currency: str) -> None:
    """Create pending payment."""
    async with Database().session() as s:
        s.add(Payments(
            provider=provider,
            external_id=external_id,
            user_id=user_id,
            amount=Decimal(amount),
            currency=currency,
            status="pending"
        ))


async def create_referral_earning(referrer_id: int, referral_id: int, amount: int, original_amount: int) -> None:
    """Create a referral credit record."""
    async with Database().session() as s:
        s.add(
            ReferralEarnings(
                referrer_id=referrer_id,
                referral_id=referral_id,
                amount=Decimal(amount),
                original_amount=Decimal(original_amount)
            )
        )


async def create_role(name: str, permissions: int) -> int | None:
    """Create a new role. Returns the new role ID, or None if name conflict."""
    async with Database().session() as s:
        result = await s.execute(select(exists().where(Role.name == name)))
        if result.scalar():
            return None
        role = Role(name=name, permissions=permissions)
        s.add(role)
        await s.flush()
        return role.id


async def create_promo_code(
    code: str,
    discount_type: str,
    discount_value,
    max_uses: int = 0,
    expires_at=None,
    item_id: int = None,
) -> int | None:
    """Create a promo code. Returns ID or None if code already exists."""
    from decimal import Decimal
    async with Database().session() as s:
        result = await s.execute(select(exists().where(PromoCodes.code == code.upper())))
        if result.scalar():
            return None
        promo = PromoCodes(
            code=code.upper(),
            discount_type=discount_type,
            discount_value=Decimal(str(discount_value)),
            max_uses=max_uses,
            expires_at=expires_at,
            item_id=item_id,
        )
        s.add(promo)
        await s.flush()
        return promo.id






async def create_review(user_id: int, item_name: str, rating: int, text: str = None) -> int | None:
    """Create a review. Returns ID or None if already reviewed."""
    async with Database().session() as s:
        existing = (await s.execute(
            select(exists().where(
                Reviews.user_id == user_id,
                Reviews.item_name == item_name
            ))
        )).scalar()
        if existing:
            return None
        review = Reviews(
            user_id=user_id,
            item_name=item_name,
            rating=rating,
            text=text,
        )
        s.add(review)
        await s.flush()
        return review.id
