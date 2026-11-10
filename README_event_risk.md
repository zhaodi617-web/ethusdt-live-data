# Observer Ladder — independent event-risk alerts

Install 3 files at the root/indicated paths of `zhaodi617-web/ethusdt-live-data` on `main`:

1. `event_risk_alert.py` at repository root.
2. `test_event_risk_alert.py` at repository root.
3. `event-risk-alert.yml` under `.github/workflows/` (not repository root).

The existing `update-okx.yml`, `observer_early.py`, `observer_v2.py`, and Telegram scripts remain unchanged. The new workflow launches when an existing **Update OKX ETH Price** workflow finishes successfully; it can also be run by hand via GitHub Actions -> **Observer Ladder Event Risk Alerts** -> **Run workflow**.

Uses existing repository secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never paste those secrets into code or chats. No trading access is needed.

For each high-impact BLS release recognized by name (CPI/PPI/employment situation), it sends an informational Telegram message at roughly T-120 and T-60 minutes, with per-event dedup recorded in `event_alert_sent.json` after a successful send. If the live BLS official .ics calendar cannot be read, it uses only a **date-scoped, confirmed** fallback: Nov 13, 2026 PPI, 13:30 UTC. It does not hallucinate other dates. Messages clarify outcome/market direction are unknown before publication.

**Important:** Github scheduling and workflow dispatch can be delayed. If a task never executes inside one of the 20-25min matching windows, this design cannot guarantee delivery. Live BLS calendar parsing is not yet verified in Github runners; check GitHub Actions logs on installation, and ensure calendar loads rather than the backup. This is a working code proposal; delivery is not verified until installed and run.

Tests (no Telegram network required): `python3 -m unittest -q test_event_risk_alert.py`.

Potential next improvement: use a provider with guaranteed near-real-time schedule and monitor alert latency; avoid putting tokens into client-facing apps.
