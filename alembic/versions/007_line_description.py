"""Let an invoice line description hold more than one line."""

import sqlalchemy as sa
from alembic import op

revision = "007_line_description"
down_revision = "006_shared_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_lines") as batch:
        batch.alter_column(
            "description",
            existing_type=sa.String(400),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("invoice_lines") as batch:
        batch.alter_column(
            "description",
            existing_type=sa.Text(),
            type_=sa.String(400),
            existing_nullable=False,
        )
