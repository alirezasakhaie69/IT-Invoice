"""Modern is the default invoice template."""

import sqlalchemy as sa
from alembic import op

revision = "008_modern_template"
down_revision = "007_line_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.alter_column(
            "template",
            existing_type=sa.String(16),
            server_default="modern",
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.alter_column(
            "template",
            existing_type=sa.String(16),
            server_default="store",
            existing_nullable=False,
        )
