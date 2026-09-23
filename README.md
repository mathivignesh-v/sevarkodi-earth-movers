# Sevarkodi Earth Movers - College Project

Python Flask + SQLite SQL management system for drivers, vehicles, work hours, earnings, reports and printable bills.

## Run
1. python -m venv venv
2. venv\Scripts\Activate.ps1
3. python -m pip install -r requirements.txt
4. python app.py
5. Open http://127.0.0.1:5000

Login: admin / admin123

The dashboard shows driver count, vehicle count, total work hours, total earnings, today's summary, quick actions and recent work records.


### Customer Ledger & Payment Tracking
- Individual customer account pages with total billing, total paid and current balance.
- Admin can add, edit and delete customer payments.
- Partial payments are supported and recorded with payment date and notes.
- Customer payment history is reflected in individual bills and printable customer statements.
- Reports can be filtered by both operator and customer.
- Example: a ₹30,000 customer balance can be reduced by a ₹10,000 payment, leaving ₹20,000 outstanding.
