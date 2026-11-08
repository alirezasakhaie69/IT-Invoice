"""Year-based document numbers and a formal accent color."""

from datetime import date

import jdatetime
import sqlalchemy as sa
from alembic import op

revision = "002_numbers"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def _code(year: int, raw) -> str | None:
    if raw is None or raw == "":
        return None
    text = str(raw)
    if "-" in text:
        return text
    return f"{year}-{int(text):03d}"


def upgrade() -> None:
    op.add_column("invoices", sa.Column("accent", sa.String(7), nullable=False, server_default="#2563eb"))
    with op.batch_alter_table("invoices") as batch:
        batch.alter_column("number", existing_type=sa.Integer(), type_=sa.String(32), existing_nullable=False)
        batch.alter_column("proforma_number", existing_type=sa.Integer(), type_=sa.String(32), existing_nullable=True)
    with op.batch_alter_table("sequences") as batch:
        batch.alter_column("name", existing_type=sa.String(16), type_=sa.String(32), existing_nullable=False)

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, doc_type, number, proforma_number, issue_date FROM invoices")).mappings()
    highest: dict[str, int] = {}
    for row in rows:
        issued = date.fromisoformat(str(row["issue_date"])[:10])
        year = jdatetime.date.fromgregorian(date=issued).year
        number = _code(year, row["number"])
        proforma = _code(year, row["proforma_number"])
        conn.execute(
            sa.text("UPDATE invoices SET number = :number, proforma_number = :proforma WHERE id = :id"),
            {"number": number, "proforma": proforma, "id": row["id"]},
        )
        serial = int(str(number).split("-")[1])
        key = f"{row['doc_type']}-{year}"
        highest[key] = max(highest.get(key, 0), serial)
    for key, last in highest.items():
        conn.execute(
            sa.text("INSERT INTO sequences (name, last_value) VALUES (:name, :last) ON CONFLICT(name) DO UPDATE SET last_value = :last"),
            {"name": key, "last": last},
        )


def downgrade() -> None:
    op.drop_column("invoices", "accent")
