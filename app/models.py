from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    locale: Mapped[str] = mapped_column(String(2), nullable=False, default="fa")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tax_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bale_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    telegram_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Issuer(Base):
    __tablename__ = "issuer"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tax_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class Sequence(Base):
    __tablename__ = "sequences"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("number", name="uq_invoice_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    proforma_number: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    template: Mapped[str] = mapped_column(String(16), nullable=False, default="modern")
    accent: Mapped[str] = mapped_column(String(7), nullable=False, default="#ea580c")
    show_store_name: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False, default="fa")
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    brief_note: Mapped[str] = mapped_column(String(280), nullable=False, default="")
    notes: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    delivery: Mapped[str] = mapped_column(String(16), nullable=False, default="not_sent")
    sending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    drive_file_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    drive_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.position",
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="Payment.paid_on, Payment.id",
    )
    send_logs: Mapped[list["SendLog"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="SendLog.id",
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(280), nullable=False, default="")
    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class SendLog(Base):
    __tablename__ = "send_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    invoice: Mapped[Invoice] = relationship(back_populates="send_logs")


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    customer: Mapped[Customer] = relationship()
    terms: Mapped[list["ContractTerm"]] = relationship(
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="ContractTerm.id",
    )


class ContractTerm(Base):
    __tablename__ = "contract_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    monthly_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="rial")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    contract: Mapped[Contract] = relationship(back_populates="terms")
    payments: Mapped[list["ContractPayment"]] = relationship(
        back_populates="term",
        cascade="all, delete-orphan",
        order_by=lambda: (ContractPayment.year, ContractPayment.month),
    )


class ContractPayment(Base):
    __tablename__ = "contract_payments"
    __table_args__ = (UniqueConstraint("term_id", "year", "month", name="uq_contract_payment_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("contract_terms.id", ondelete="CASCADE"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    term: Mapped[ContractTerm] = relationship(back_populates="payments")


class DriveSetting(Base):
    __tablename__ = "drive_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
