"""Initial schema."""

from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("locale", sa.String(2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(80), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("tax_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("bale_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("telegram_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "issuer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("phone", sa.String(80), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("tax_id", sa.String(64), nullable=False, server_default=""),
    )
    op.create_table(
        "sequences",
        sa.Column("name", sa.String(16), primary_key=True),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_type", sa.String(16), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("proforma_number", sa.Integer(), nullable=True, unique=True),
        sa.Column("template", sa.String(16), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("brief_note", sa.String(280), nullable=False, server_default=""),
        sa.Column("delivery", sa.String(16), nullable=False),
        sa.Column("sending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("drive_file_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("drive_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("doc_type", "number", name="uq_invoice_number"),
    )
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(400), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tax_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(80), nullable=False, server_default=""),
        sa.Column("note", sa.String(280), nullable=False, server_default=""),
    )
    op.create_table(
        "send_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "drive_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False, server_default=""),
    )
    op.bulk_insert(
        sa.table("sequences", sa.column("name", sa.String), sa.column("last_value", sa.Integer)),
        [{"name": "invoice", "last_value": 0}, {"name": "proforma", "last_value": 0}],
    )


def downgrade() -> None:
    for name in (
        "drive_settings", "send_logs", "payments", "invoice_lines", "invoices",
        "sequences", "issuer", "customers", "login_attempts", "sessions", "users",
    ):
        op.drop_table(name)
