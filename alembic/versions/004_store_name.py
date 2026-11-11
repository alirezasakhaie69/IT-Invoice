"""Optional store name on the invoice."""

import sqlalchemy as sa
from alembic import op

revision = "004_store_name"
down_revision = "003_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("show_store_name", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("invoices", "show_store_name")
