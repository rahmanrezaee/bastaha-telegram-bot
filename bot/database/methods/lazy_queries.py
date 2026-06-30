from typing import Any
from sqlalchemy import func, select
from sqlalchemy import desc
from bot.database import Database
from bot.database.models import (
    Goods, User, BoughtGoods, ItemValues,
    ReferralEarnings, Role, Operations
)
from bot.database.models.main import PromoCodes, Reviews


async def query_all_items(offset: int = 0, limit: int = 10, count_only: bool = False) -> Any:
    """Query all items with pagination (returns name, price, stock)"""
    async with Database().session() as s:
        if count_only:
            return (await s.execute(select(func.count(Goods.id)))).scalar() or 0
        
        # Subquery to count items
        stock_subq = (
            select(
                ItemValues.item_id,
                func.count(ItemValues.id).label("stock")
            )
            .group_by(ItemValues.item_id)
            .subquery()
        )
        
        stmt = (
            select(
                Goods.name,
                Goods.price,
                func.coalesce(stock_subq.c.stock, 0).label("stock")
            )
            .outerjoin(stock_subq, Goods.id == stock_subq.c.item_id)
            .order_by(Goods.name.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await s.execute(stmt)
        return [{"name": row.name, "price": float(row.price), "stock": row.stock} for row in result.all()]


async def query_user_bought_items(user_id: int, offset: int = 0, limit: int = 10, count_only: bool = False) -> Any:
    """Query user's bought items with pagination"""
    async with Database().session() as s:
        if count_only:
            return (await s.execute(
                select(func.count()).select_from(BoughtGoods).where(BoughtGoods.buyer_id == user_id)
            )).scalar() or 0
        result = await s.execute(
            select(BoughtGoods)
            .where(BoughtGoods.buyer_id == user_id)
            .order_by(desc(BoughtGoods.bought_datetime))
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()


async def query_all_users(offset: int = 0, limit: int = 10, count_only: bool = False) -> Any:
    """Query all users with pagination"""
    async with Database().session() as s:
        if count_only:
            return (await s.execute(select(func.count(User.telegram_id)))).scalar() or 0
        result = await s.execute(
            select(User.telegram_id)
            .order_by(User.telegram_id.asc())
            .offset(offset)
            .limit(limit)
        )
        return [row[0] for row in result.all()]


async def query_items_in_position(item_name: str, offset: int = 0, limit: int = 10, count_only: bool = False) -> Any:
    """Query items in position with pagination"""
    async with Database().session() as s:
        item_id = (await s.execute(
            select(Goods.id).where(Goods.name == item_name)
        )).scalar()
        if not item_id:
            return 0 if count_only else []
        query = select(ItemValues.id).where(ItemValues.item_id == item_id)
        if count_only:
            count_result = await s.execute(select(func.count()).select_from(query.subquery()))
            return count_result.scalar() or 0
        result = await s.execute(
            query.order_by(ItemValues.id.asc()).offset(offset).limit(limit)
        )
        return [row[0] for row in result.all()]


async def query_user_referrals(user_id: int, offset: int = 0, limit: int = 10, count_only: bool = False) -> Any:
    """Query user's referrals with earnings info"""
    async with Database().session() as s:
        if count_only:
            return (await s.execute(
                select(func.count(User.telegram_id)).where(User.referral_id == user_id)
            )).scalar() or 0

        earnings_subq = (
            select(
                ReferralEarnings.referral_id,
                func.coalesce(func.sum(ReferralEarnings.amount), 0).label('total_earned')
            )
            .where(ReferralEarnings.referrer_id == user_id)
            .group_by(ReferralEarnings.referral_id)
            .subquery()
        )

        stmt = (
            select(
                User.telegram_id,
                User.registration_date,
                func.coalesce(earnings_subq.c.total_earned, 0).label('total_earned')
            )
            .outerjoin(earnings_subq, User.telegram_id == earnings_subq.c.referral_id)
            .where(User.referral_id == user_id)
            .order_by(desc(func.coalesce(earnings_subq.c.total_earned, 0)))
            .offset(offset)
            .limit(limit)
        )
        rows = (await s.execute(stmt)).all()

        return [
            {
                'telegram_id': row.telegram_id,
                'registration_date': row.registration_date,
                'total_earned': row.total_earned
            }
            for row in rows
        ]


async def query_referral_earnings_from_user(referrer_id: int, referral_id: int, offset: int = 0, limit: int = 10,
                                            count_only: bool = False) -> Any:
    """Query earnings from specific referral"""
    async with Database().session() as s:
        base = select(ReferralEarnings).where(
            ReferralEarnings.referrer_id == referrer_id,
            ReferralEarnings.referral_id == referral_id
        )
        if count_only:
            count_result = await s.execute(select(func.count()).select_from(base.subquery()))
            return count_result.scalar() or 0
        result = await s.execute(
            base.order_by(desc(ReferralEarnings.created_at)).offset(offset).limit(limit)
        )
        return result.scalars().all()


async def query_all_referral_earnings(referrer_id: int, offset: int = 0, limit: int = 10,
                                      count_only: bool = False) -> Any:
    """Query all referral earnings for user"""
    async with Database().session() as s:
        base = select(ReferralEarnings).where(
            ReferralEarnings.referrer_id == referrer_id
        )
        if count_only:
            count_result = await s.execute(select(func.count()).select_from(base.subquery()))
            return count_result.scalar() or 0
        result = await s.execute(
            base.order_by(desc(ReferralEarnings.created_at)).offset(offset).limit(limit)
        )
        return result.scalars().all()


async def query_promo_codes(offset: int = 0, limit: int = 10, count_only: bool = False) -> Any:
    """Query promo codes with pagination"""
    async with Database().session() as s:
        if count_only:
            return (await s.execute(select(func.count(PromoCodes.id)))).scalar() or 0
        result = await s.execute(
            select(PromoCodes)
            .order_by(desc(PromoCodes.created_at))
            .offset(offset)
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                'id': p.id, 'code': p.code, 'discount_type': p.discount_type,
                'discount_value': p.discount_value, 'max_uses': p.max_uses,
                'current_uses': p.current_uses, 'is_active': p.is_active,
                'expires_at': p.expires_at, 'created_at': p.created_at,
            }
            for p in rows
        ]



async def query_user_operations_history(user_id: int, offset: int = 0, limit: int = 10,
                                        count_only: bool = False) -> Any:
    """Query user's full operations history (topups, purchases, referral bonuses) as UNION ALL"""
    from sqlalchemy import literal_column, union_all, literal
    async with Database().session() as s:
        # 1. Top-ups (operations with positive value)
        topups = (
            select(
                Operations.id,
                literal('topup').label('type'),
                Operations.operation_value.label('amount'),
                Operations.operation_time.label('date'),
            )
            .where(Operations.user_id == user_id, Operations.operation_value > 0)
        )
        # 2. Purchases
        purchases = (
            select(
                BoughtGoods.id,
                literal('purchase').label('type'),
                (-BoughtGoods.price).label('amount'),
                BoughtGoods.bought_datetime.label('date'),
            )
            .where(BoughtGoods.buyer_id == user_id)
        )
        # 3. Referral earnings
        referrals = (
            select(
                ReferralEarnings.id,
                literal('referral').label('type'),
                ReferralEarnings.amount.label('amount'),
                ReferralEarnings.created_at.label('date'),
            )
            .where(ReferralEarnings.referrer_id == user_id)
        )

        combined = union_all(topups, purchases, referrals).subquery()

        if count_only:
            return (await s.execute(select(func.count()).select_from(combined))).scalar() or 0

        result = await s.execute(
            select(combined).order_by(combined.c.date.desc()).offset(offset).limit(limit)
        )
        return [
            {
                'id': row.id,
                'type': row.type,
                'amount': row.amount,
                'date': row.date,
            }
            for row in result.all()
        ]


async def query_item_reviews(item_name: str, offset: int = 0, limit: int = 10,
                             count_only: bool = False) -> Any:
    """Query reviews for an item with pagination"""
    async with Database().session() as s:
        base = select(Reviews).where(Reviews.item_name == item_name)
        if count_only:
            count_q = select(func.count()).select_from(base.subquery())
            return (await s.execute(count_q)).scalar() or 0
        result = await s.execute(
            base.order_by(desc(Reviews.created_at)).offset(offset).limit(limit)
        )
        return [
            {
                'id': r.id, 'user_id': r.user_id, 'rating': r.rating,
                'text': r.text, 'created_at': r.created_at,
            }
            for r in result.scalars().all()
        ]
