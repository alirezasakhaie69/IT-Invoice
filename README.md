# Invoice service

A small dark-only web app for issuing invoices and pro formas, recording payments, exporting PDFs, and sending them through Bale and Telegram. It runs with Docker. Nothing in the invoice is loaded from a CDN.

## Setup

1. Install Docker.
2. Copy the environment file and replace the placeholders. Do this on your machine. Do not paste the values into chat.

```bash
cp .env.example .env
```

3. Start the app:

```bash
docker compose up --build
```

4. Open http://localhost:9050 and sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`.

The first boot creates that admin when the user table is empty. The container clock uses `Asia/Tehran`, which is what “today” means for new invoices and clones. Sessions are checked in UTC and last 3 hours from login. Later requests do not extend them.

Data (SQLite, PDFs, encrypted Drive settings) stays on the `invoice_data` volume at `/data`.

### Tests

```bash
docker compose run --rm web python -m unittest discover -s tests -v
```

### Sample PDFs

```bash
docker compose run --rm web python -m app.samples
```

This writes `samples/invoice-fa-store.pdf` and `samples/invoice-en-formal.pdf`.

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_SECRET_KEY` | yes | Signs the login cookie and encrypts Drive credentials. |
| `ADMIN_USERNAME` | first boot | First admin username. |
| `ADMIN_PASSWORD` | first boot | First admin password. Stored only as an argon2 hash. |
| `BALE_BOT_TOKEN` | to send on Bale | Token from Bale’s bot father. Leave empty until you have one. |
| `TELEGRAM_BOT_TOKEN` | to send on Telegram | Token from BotFather. Leave empty until you have one. |
| `TELEGRAM_PROXY_URL` | no | HTTP proxy used only for Telegram, for example `http://127.0.0.1:8080`. |
| `BALE_API_BASE` | no | Default `https://tapi.bale.ai`. |
| `TELEGRAM_API_BASE` | no | Default `https://api.telegram.org`. |
| `APP_BASE_URL` | no | Public origin, default `http://localhost:9050`. Used as the OAuth redirect base. |
| `DATA_DIR` | no | Default `/data` in Compose. |

The app does not print these values.

## Bots

Sending uses the official document methods:

- Bale: `POST {BALE_API_BASE}/bot<token>/sendDocument` ([docs.bale.ai](https://docs.bale.ai/))
- Telegram: `POST {TELEGRAM_API_BASE}/bot<token>/sendDocument` ([Telegram Bot API](https://core.telegram.org/bots/api#senddocument))

The PDF is uploaded as multipart form data. Each attempt is stored with the time, channel, result, and error. A send is marked sent only when every channel that was attempted succeeds. A failed retry does not clear an earlier sent state. You can also set sent or not sent by hand.

**A customer must have started the bot before it can message them.** If they have not, the bot API returns an error and the invoice stays not sent.

**Telegram may need `TELEGRAM_PROXY_URL` on some networks.** It must be an HTTP proxy URL. Bale is not sent through that proxy.

A successful retry can deliver a second copy of the PDF. The local file is still kept.

## Google Drive

Open Settings as an admin.

1. Enter the seller details used on the PDF.
2. Set a folder path such as `Invoices/{year}` or `Invoices/{year}/{month}`. Missing folders are created. `{year}` and `{month}` come from the invoice issue date in that invoice’s calendar (Solar Hijri for Persian, Gregorian for English).
3. Choose a connection and use Test connection.
4. Optional: save a copy to Drive automatically after a successful send. A local PDF is always kept. A Drive failure does not undo the send.

### Personal Drive (OAuth)

Create an OAuth client (Web application) in Google Cloud, enable the Drive API, and register this redirect URL exactly:

`{APP_BASE_URL}/settings/drive/oauth/callback`

The app requests `https://www.googleapis.com/auth/drive` because the narrower `drive.file` scope cannot see a folder the app did not create ([scope guide](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)). That scope is restricted. For a private client, add your Google account as a test user while the app is in testing mode.

Paste the client id and secret in Settings, save, then use Connect Google. Tokens are encrypted with `APP_SECRET_KEY` and are not shown again. Leave a secret field blank to keep the saved value.

### Service account (Shared Drive only)

A service account cannot own files and cannot upload into My Drive. Google returns `storageQuotaExceeded`. Upload only into a Shared Drive ([folder guide](https://developers.google.com/workspace/drive/api/guides/folder), [shared drives](https://developers.google.com/workspace/drive/api/guides/about-shareddrives)).

1. Create a service account and a JSON key.
2. Add the service account email to the Shared Drive as Contributor or Content manager.
3. Paste the Shared Drive id and the JSON key in Settings.

Uploads send `supportsAllDrives=true`. Files are created with `POST https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart` ([files.create](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create)). Each save adds a new file. It does not replace the previous one.

## How invoices work

- Amounts are integers in the invoice’s own unit, rial or toman. They are never converted. One toman is 10 rial only as a definition. Totals on the summary are grouped by unit.
- Quantity is a positive whole number. Discount is an amount in the same unit. Tax is a percent from 0 to 100. Line tax rounds half up.
- Invoices and pro formas have separate yearly numbers such as `1405-001`, taken from the Solar Hijri year of the issue date. The number can be edited. Converting a pro forma gives it the next invoice number, keeps the pro forma number on the page, sets delivery back to not sent, and keeps recorded payments so they count as receivables. A sent invoice can be sent again.
- Clone copies the customer, lines, currency, document type, and template. The language follows your current UI language. The issue date is today. Due date and the brief note are cleared. Delivery is not sent and there are no payments. The new document opens in the editor.
- The brief note is shown in the list and on the detail page. It is not in the PDF. The invoice description is printed at the bottom of the PDF.
- Payment status is derived: unpaid, partially paid, or paid (including a zero total, or an overpayment). Outstanding is the total minus payments and can be negative.
- The summary uses issue date and skips pro formas. CSV contains the invoice rows and the per-currency totals as plain integers.
- Persian is the default, right to left, with the Solar Hijri calendar. English is left to right with the Gregorian calendar. Dates are stored as Gregorian ISO. Digits and thousands separators go through one formatter: `1,000,000` in English and `۱,۰۰۰,۰۰۰` in Persian.
- Admins manage users and Drive settings. You cannot delete yourself or the last admin. Login allows 5 failures per IP and username per 15 minutes. POSTs require a CSRF token.

## PDF

Each invoice is Store (monochrome pad) or Formal (blue `#2563eb`). Both are A4, use the vendored Vazirmatn font, and follow the invoice language. The sheet is white. The application around it is dark only.

Deviations from the [Factor-Builder](https://github.com/Alirewa/Factor-Builder) templates:

- The brief note stays off the PDF. The invoice description (توضیحات) is printed at the bottom when it is filled in.
- Due date is an extra meta row.
- Discount and tax percent are columns on each line. The totals still show the summed discount and tax.
- No logo, stamp, signature, «بسمه تعالی», email, or custom footer.
- Amount in words is printed. It is calculated from the total and is not a separate field.
- The app UI is dark. The PDF page stays the sample’s white sheet.

## Known limitations

- A customer must start the bot before it can be messaged.
- Telegram may need `TELEGRAM_PROXY_URL` on some networks. SOCKS proxies are not supported.
- A retry can deliver another copy of the file.
- Service accounts work only with a Shared Drive.
- The OAuth client must be allowed to use the full Drive scope.
- Sessions end 3 hours after login and are not extended by activity.
- Quantity is a whole number and may be negative, which subtracts a return from the total. Money is not converted between rial and toman. Bot tokens can be saved in Settings; a blank field keeps the environment variable. Downloaded and Drive PDFs are named with the document number and the customer name. The formal PDF color is chosen on the invoice.
- There is one seller profile, no logo, and no light theme.
- This is a single-server app. The login limit and the send lock live in SQLite.

## Stack

Python 3.12, FastAPI, Jinja2, HTMX, SQLAlchemy, Alembic, SQLite, WeasyPrint, jdatetime, argon2-cffi, uvicorn, python-multipart, and cryptography (credential encryption and the service-account signature). Bot and Drive calls use the Python standard library.

Vazirmatn is vendored under the SIL Open Font License (`app/static/fonts/OFL.txt`). HTMX 2.0.4 is vendored in `app/static/js`.
