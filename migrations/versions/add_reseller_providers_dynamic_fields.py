"""add reseller providers dynamic fields

Revision ID: add_reseller_providers_dynamic_fields
Revises: add_reseller_tables
Create Date: 2026-07-02 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = 'add_reseller_providers_dynamic_fields'
down_revision: Union[str, None] = 'add_reseller_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    insp = inspect(conn)
    if table_name not in insp.get_table_names():
        return False
    columns = [c['name'] for c in insp.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    bind = op.get_bind()
    
    # 1. products_url
    if not _column_exists(bind, 'reseller_providers', 'products_url'):
        op.add_column('reseller_providers', sa.Column('products_url', sa.String(length=255), nullable=True))
        
    # 2. products_path
    if not _column_exists(bind, 'reseller_providers', 'products_path'):
        op.add_column('reseller_providers', sa.Column('products_path', sa.String(length=100), nullable=True))
        
    # 3. product_id_path
    if not _column_exists(bind, 'reseller_providers', 'product_id_path'):
        op.add_column('reseller_providers', sa.Column('product_id_path', sa.String(length=100), nullable=True))
        
    # 4. product_name_path
    if not _column_exists(bind, 'reseller_providers', 'product_name_path'):
        op.add_column('reseller_providers', sa.Column('product_name_path', sa.String(length=100), nullable=True))
        
    # 5. product_price_path
    if not _column_exists(bind, 'reseller_providers', 'product_price_path'):
        op.add_column('reseller_providers', sa.Column('product_price_path', sa.String(length=100), nullable=True))
        
    # 6. product_stock_path
    if not _column_exists(bind, 'reseller_providers', 'product_stock_path'):
        op.add_column('reseller_providers', sa.Column('product_stock_path', sa.String(length=100), nullable=True))
        
    # 7. purchase_url
    if not _column_exists(bind, 'reseller_providers', 'purchase_url'):
        op.add_column('reseller_providers', sa.Column('purchase_url', sa.String(length=255), nullable=True))
        
    # 8. purchase_method
    if not _column_exists(bind, 'reseller_providers', 'purchase_method'):
        op.add_column('reseller_providers', sa.Column('purchase_method', sa.String(length=10), nullable=True, server_default='POST'))
        
    # 9. purchase_payload_template
    if not _column_exists(bind, 'reseller_providers', 'purchase_payload_template'):
        op.add_column('reseller_providers', sa.Column('purchase_payload_template', sa.Text(), nullable=True))
        
    # 10. purchase_headers
    if not _column_exists(bind, 'reseller_providers', 'purchase_headers'):
        op.add_column('reseller_providers', sa.Column('purchase_headers', sa.Text(), nullable=True))
        
    # 11. purchase_order_id_path
    if not _column_exists(bind, 'reseller_providers', 'purchase_order_id_path'):
        op.add_column('reseller_providers', sa.Column('purchase_order_id_path', sa.String(length=100), nullable=True))
        
    # 12. purchase_credentials_path
    if not _column_exists(bind, 'reseller_providers', 'purchase_credentials_path'):
        op.add_column('reseller_providers', sa.Column('purchase_credentials_path', sa.String(length=100), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    
    if _column_exists(bind, 'reseller_providers', 'purchase_credentials_path'):
        op.drop_column('reseller_providers', 'purchase_credentials_path')
    if _column_exists(bind, 'reseller_providers', 'purchase_order_id_path'):
        op.drop_column('reseller_providers', 'purchase_order_id_path')
    if _column_exists(bind, 'reseller_providers', 'purchase_headers'):
        op.drop_column('reseller_providers', 'purchase_headers')
    if _column_exists(bind, 'reseller_providers', 'purchase_payload_template'):
        op.drop_column('reseller_providers', 'purchase_payload_template')
    if _column_exists(bind, 'reseller_providers', 'purchase_method'):
        op.drop_column('reseller_providers', 'purchase_method')
    if _column_exists(bind, 'reseller_providers', 'purchase_url'):
        op.drop_column('reseller_providers', 'purchase_url')
    if _column_exists(bind, 'reseller_providers', 'product_stock_path'):
        op.drop_column('reseller_providers', 'product_stock_path')
    if _column_exists(bind, 'reseller_providers', 'product_price_path'):
        op.drop_column('reseller_providers', 'product_price_path')
    if _column_exists(bind, 'reseller_providers', 'product_name_path'):
        op.drop_column('reseller_providers', 'product_name_path')
    if _column_exists(bind, 'reseller_providers', 'product_id_path'):
        op.drop_column('reseller_providers', 'product_id_path')
    if _column_exists(bind, 'reseller_providers', 'products_path'):
        op.drop_column('reseller_providers', 'products_path')
    if _column_exists(bind, 'reseller_providers', 'products_url'):
        op.drop_column('reseller_providers', 'products_url')
