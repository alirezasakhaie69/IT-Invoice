"""One yearly number sequence for invoices and pro formas."""

import sqlalchemy as sa
from alembic import op

revision = "006_shared_numbers"
down_revision = "005_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    highest: dict[str, int] = {}
    for row in conn.execute(sa.text("SELECT name, last_value FROM sequences")).mappings():
        name = str(row["name"])
        if "-" not in name:
            continue
        kind, year = name.rsplit("-", 1)
        if kind in ("invoice", "proforma", "doc") and year.isdigit():
            key = f"doc-{year}"
            highest[key] = max(highest.get(key, 0), int(row["last_value"]))
    for number in conn.execute(sa.text("SELECT number FROM invoices")).scalars():
        text = str(number or "")
        if "-" not in text:
            continue
        year, serial = text.split("-", 1)
        if year.isdigit() and serial.isdigit():
            key = f"doc-{year}"
            highest[key] = max(highest.get(key, 0), int(serial))
    for key, last in highest.items():
        conn.execute(
            sa.text(
                "INSERT INTO sequences (name, last_value) VALUES (:name, :last) "
                "ON CONFLICT(name) DO UPDATE SET last_value = :last"
            ),
            {"name": key, "last": last},
        )
    with op.batch_alter_table("invoices") as batch:
        batch.drop_constraint("uq_invoice_number", type_="unique")
        batch.create_unique_constraint("uq_invoice_number", ["number"])


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.drop_constraint("uq_invoice_number", type_="unique")
        batch.create_unique_constraint("uq_invoice_number", ["doc_type", "number"])
