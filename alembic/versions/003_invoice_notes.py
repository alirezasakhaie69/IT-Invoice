"""Invoice description printed on the PDF."""

import sqlalchemy as sa
from alembic import op

revision = "003_notes"
down_revision = "002_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("notes", sa.String(400), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("invoices", "notes")
