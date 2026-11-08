import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("APP_SECRET_KEY", "unit-test-secret")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from sqlalchemy.orm import sessionmaker

from app.auth import create_session, get_session, hash_password, session_expiry
from app.db import Base, make_engine
from app.models import SessionRow, User


class SessionExpiryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        url = "sqlite:///" + (Path(self.tmp.name) / "t.db").as_posix()
        self.engine = make_engine(url)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_three_hour_absolute_expiry(self):
        login_at = datetime(2026, 1, 1, 12, 0, 0)
        user = User(
            username="admin",
            password_hash=hash_password("test-password"),
            role="admin",
            locale="fa",
            created_at=login_at,
        )
        self.db.add(user)
        self.db.commit()
        row = create_session(self.db, user, login_at)
        original = row.expires_at
        self.assertEqual(original, session_expiry(login_at))
        self.assertEqual(original, login_at + timedelta(hours=3))

        later = get_session(self.db, row.id, login_at + timedelta(hours=1))
        self.assertIsNotNone(later)
        self.db.expire(later)
        reloaded = self.db.get(SessionRow, row.id)
        self.assertEqual(reloaded.expires_at, original)

        still = get_session(self.db, row.id, login_at + timedelta(hours=2, minutes=59))
        self.assertIsNotNone(still)
        self.assertEqual(still.expires_at, original)

        expired = get_session(self.db, row.id, login_at + timedelta(hours=3))
        self.assertIsNone(expired)
        self.assertIsNone(self.db.get(SessionRow, row.id))


if __name__ == "__main__":
    unittest.main()
