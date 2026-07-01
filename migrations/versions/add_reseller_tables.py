"""add reseller tables

Revision ID: add_reseller_tables
Revises: e5f6a7b8c9d0
Create Date: 2026-07-01 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = 'add_reseller_tables'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    # 1. reseller_providers
    if 'reseller_providers' not in existing_tables:
        op.create_table('reseller_providers',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('base_url', sa.String(length=255), nullable=False),
            sa.Column('api_key', sa.String(length=255), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
            sa.Column('markup_percent', sa.Numeric(precision=6, scale=2), nullable=False, default=0.00),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name')
        )
        op.create_index('ix_reseller_providers_is_active', 'reseller_providers', ['is_active'])

    # 2. reseller_products
    if 'reseller_products' not in existing_tables:
        op.create_table('reseller_products',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('provider_id', sa.Integer(), nullable=False),
            sa.Column('upstream_id', sa.String(length=100), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('original_price', sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column('stock', sa.Integer(), nullable=False, default=0),
            sa.Column('mapped_goods_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['provider_id'], ['reseller_providers.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['mapped_goods_id'], ['goods.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('provider_id', 'upstream_id', name='uq_provider_upstream_product')
        )
        op.create_index('ix_reseller_products_provider_id', 'reseller_products', ['provider_id'])
        op.create_index('ix_reseller_products_mapped_goods_id', 'reseller_products', ['mapped_goods_id'])

    # 3. reseller_orders
    if 'reseller_orders' not in existing_tables:
        op.create_table('reseller_orders',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('bought_goods_id', sa.Integer(), nullable=True),
            sa.Column('provider_id', sa.Integer(), nullable=True),
            sa.Column('upstream_product_id', sa.String(length=100), nullable=False),
            sa.Column('idempotency_key', sa.String(length=100), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, default='pending'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('upstream_order_id', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['bought_goods_id'], ['bought_goods.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['provider_id'], ['reseller_providers.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('idempotency_key')
        )
        op.create_index('ix_reseller_orders_bought_goods_id', 'reseller_orders', ['bought_goods_id'])
        op.create_index('ix_reseller_orders_provider_id', 'reseller_orders', ['provider_id'])
        op.create_index('ix_reseller_orders_status', 'reseller_orders', ['status'])
        op.create_index('ix_reseller_orders_created_at', 'reseller_orders', ['created_at'])


def downgrade() -> None:
    op.drop_table('reseller_orders')
    op.drop_table('reseller_products')
    op.drop_table('reseller_providers')
