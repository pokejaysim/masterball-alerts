#!/usr/bin/env python3
"""Local-only MasterBall status page."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import sys
from urllib.parse import urlparse

from status_health import DEFAULT_STALE_MINUTES, build_snapshot


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def _json_default(value):
    return str(value)


def html_page(snapshot: dict, refresh_seconds: int) -> str:
    initial_json = html.escape(json.dumps(snapshot, default=_json_default), quote=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MasterBall Status</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-2: #eef2f6;
      --text: #17202a;
      --muted: #647184;
      --border: #d8dee8;
      --ok: #12805c;
      --ok-bg: #e8f6ef;
      --degraded: #a35b00;
      --degraded-bg: #fff2d8;
      --down: #b3261e;
      --down-bg: #fde7e4;
      --watch: #315f9d;
      --watch-bg: #e8f0fb;
      --paused: #315f9d;
      --paused-bg: #e8f0fb;
      --shadow: 0 18px 45px rgba(25, 35, 50, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
      line-height: 1.45;
    }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 44px;
    }}
    header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 8px 0 0;
      color: var(--muted);
      max-width: 720px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 128px;
      min-height: 40px;
      padding: 9px 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-weight: 800;
      text-transform: uppercase;
      font-size: 13px;
      letter-spacing: .04em;
      white-space: nowrap;
    }}
    .status-ok {{ color: var(--ok); background: var(--ok-bg); border-color: #acdcca; }}
    .status-degraded {{ color: var(--degraded); background: var(--degraded-bg); border-color: #efc574; }}
    .status-down {{ color: var(--down); background: var(--down-bg); border-color: #f2aaa4; }}
    .status-watch {{ color: var(--watch); background: var(--watch-bg); border-color: #b8cdeb; }}
    .status-paused {{ color: var(--paused); background: var(--paused-bg); border-color: #b8cdeb; }}
    .grid {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 16px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 18px;
    }}
    .panel h2 {{
      margin: 0 0 14px;
      font-size: 16px;
      line-height: 1.2;
    }}
    .hero {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .metric {{
      min-height: 92px;
      background: var(--surface-2);
      border: 1px solid #e3e8ef;
      border-radius: 8px;
      padding: 13px;
    }}
    .metric .label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .metric .value {{
      display: block;
      margin-top: 8px;
      font-size: 24px;
      font-weight: 800;
      line-height: 1;
    }}
    .metric .hint {{
      display: block;
      color: var(--muted);
      margin-top: 8px;
      font-size: 13px;
    }}
    .message {{
      margin: 0;
      font-size: 18px;
      font-weight: 750;
    }}
    .meta {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      text-align: left;
      border-bottom: 1px solid var(--border);
      padding: 10px 8px;
      vertical-align: middle;
      font-size: 14px;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
      font-weight: 800;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .mini-status {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 76px;
      padding: 5px 8px;
      border-radius: 7px;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .03em;
    }}
    .commands {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }}
    code {{
      display: block;
      overflow-wrap: anywhere;
      background: #17202a;
      color: #f8fafc;
      padding: 10px 12px;
      border-radius: 7px;
      font-size: 13px;
    }}
    .log {{
      max-height: 330px;
      overflow: auto;
      background: #101820;
      color: #d7e0ea;
      border-radius: 8px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
    }}
    .wide {{ grid-column: 1 / -1; }}
    .button-row {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    button, a.button {{
      appearance: none;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      color: var(--text);
      min-height: 38px;
      padding: 8px 12px;
      font-weight: 750;
      font-size: 14px;
      text-decoration: none;
      cursor: pointer;
    }}
    button:hover, a.button:hover {{ background: var(--surface-2); }}
    .small {{
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 860px) {{
      .grid, .hero {{ grid-template-columns: 1fr; }}
      header {{ flex-direction: column; }}
      .pill {{ align-self: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>MasterBall Status</h1>
        <p class="subtitle">Private local dashboard for the Mac Mini monitor, discovery queue, retailer health, and recent log activity.</p>
      </div>
      <div id="overall-pill" class="pill status-watch">Loading</div>
    </header>

    <div class="grid">
      <section class="panel wide">
        <p id="message" class="message">Loading service status...</p>
        <div id="meta" class="meta"></div>
        <div class="hero">
          <div class="metric">
            <span class="label">Service</span>
            <span id="service-value" class="value">--</span>
            <span id="service-hint" class="hint">Checking LaunchAgent</span>
          </div>
          <div class="metric">
            <span class="label">Last Activity</span>
            <span id="last-activity" class="value">--</span>
            <span id="activity-hint" class="hint">Monitor log heartbeat</span>
          </div>
          <div class="metric">
            <span class="label">Active Products</span>
            <span id="active-products" class="value">--</span>
            <span id="product-hint" class="hint">Seed plus approved discoveries</span>
          </div>
          <div class="metric">
            <span class="label">Today</span>
            <span id="alerts-today" class="value">--</span>
            <span class="hint">Stock alerts sent</span>
          </div>
        </div>
        <div class="button-row">
          <button type="button" id="refresh">Refresh now</button>
          <a class="button" href="/api/status">JSON status</a>
          <a class="button" href="/healthz">Health check</a>
        </div>
      </section>

      <section class="panel">
        <h2>Retailers</h2>
        <table>
          <thead><tr><th>Retailer</th><th>Status</th><th>Signals</th></tr></thead>
          <tbody id="retailers"></tbody>
        </table>
      </section>

      <section class="panel">
        <h2>Discovery Queue</h2>
        <div class="hero" style="grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 0;">
          <div class="metric">
            <span class="label">Approved</span>
            <span id="approved" class="value">--</span>
            <span class="hint">Auto-loaded into monitor</span>
          </div>
          <div class="metric">
            <span class="label">Pending</span>
            <span id="pending" class="value">--</span>
            <span class="hint">Waiting for review</span>
          </div>
          <div class="metric">
            <span class="label">Walmart Lane</span>
            <span id="walmart-lane" class="value">--</span>
            <span id="walmart-lane-hint" class="hint">Protected checker</span>
          </div>
          <div class="metric">
            <span class="label">Walmart Queue</span>
            <span id="walmart-queue" class="value">--</span>
            <span id="walmart-queue-hint" class="hint">Pending validation</span>
          </div>
        </div>
        <div class="commands" id="actions"></div>
      </section>

      <section class="panel wide">
        <h2>Recent Log</h2>
        <div id="last-cycle" class="small"></div>
        <pre id="log" class="log"></pre>
      </section>
    </div>
  </div>

  <script id="initial-data" type="application/json">{initial_json}</script>
  <script>
    const refreshSeconds = {int(refresh_seconds)};
    const statusLabels = {{
      ok: "OK",
      degraded: "Degraded",
      down: "Down",
      watch: "Watch",
      paused: "Paused"
    }};

    function text(id, value) {{
      document.getElementById(id).textContent = value ?? "--";
    }}

    function ageLabel(seconds) {{
      if (seconds === null || seconds === undefined) return "No signal";
      if (seconds < 60) return Math.max(0, Math.round(seconds)) + "s ago";
      if (seconds < 3600) return Math.round(seconds / 60) + "m ago";
      return Math.round(seconds / 3600) + "h ago";
    }}

    function statusClass(status) {{
      return "status-" + (status || "watch");
    }}

    function displayStatus(status) {{
      return ["ok", "degraded", "down", "watch", "paused"].includes(status) ? status : "watch";
    }}

    function render(snapshot) {{
      const overall = snapshot.overall || "watch";
      const pill = document.getElementById("overall-pill");
      pill.className = "pill " + statusClass(overall);
      pill.textContent = statusLabels[overall] || overall;

      text("message", snapshot.message);
      text("meta", "Updated " + snapshot.generated_at + " | Refreshes every " + refreshSeconds + "s");

      const service = snapshot.service || {{}};
      text("service-value", service.running === true ? "Running" : service.running === false ? "Stopped" : "Unknown");
      text("service-hint", service.pid ? "PID " + service.pid + " | " + service.state : service.state || "No process state");

      text("last-activity", ageLabel(snapshot.log && snapshot.log.last_seen_age_seconds));
      text("activity-hint", snapshot.log && snapshot.log.last_seen_at ? "Last log at " + snapshot.log.last_seen_at : "No log timestamp found");
      text("active-products", snapshot.products ? snapshot.products.active_total : "--");
      text("product-hint", snapshot.products ? snapshot.products.seed_enabled + " seed + " + snapshot.products.approved_dynamic + " approved" : "--");
      text("alerts-today", snapshot.database ? snapshot.database.alerts_today : "--");

      const discovery = snapshot.database && snapshot.database.discovery ? snapshot.database.discovery : {{}};
      text("approved", discovery.approved);
      text("pending", discovery.pending);
      const walmart = snapshot.walmart || {{}};
      text("walmart-lane", walmart.lane_state ? walmart.lane_state.toUpperCase() : "--");
      text("walmart-lane-hint", walmart.proxy_configured ? "Proxy configured | " + (walmart.active_product_count || 0) + " active" : "Proxy missing | " + (walmart.active_product_count || 0) + " active");
      text("walmart-queue", walmart.pending_validation_count ?? walmart.pending_count ?? "--");
      text("walmart-queue-hint", (walmart.pending_count || 0) + " total pending Walmart");

      const retailers = document.getElementById("retailers");
      retailers.innerHTML = "";
      (snapshot.retailers || []).forEach((row) => {{
        const tr = document.createElement("tr");
        const shownStatus = displayStatus(row.status);
        tr.innerHTML = `
          <td>${{row.name}}</td>
          <td><span class="mini-status ${{statusClass(shownStatus)}}">${{row.status}}</span></td>
          <td>${{row.note}}<br><span class="small">${{row.success}} success / ${{row.blocked}} blocked / ${{row.errors}} errors</span></td>
        `;
        retailers.appendChild(tr);
      }});

      const actions = document.getElementById("actions");
      actions.innerHTML = "";
      (snapshot.actions || []).forEach((command) => {{
        const code = document.createElement("code");
        code.textContent = command;
        actions.appendChild(code);
      }});

      text("last-cycle", snapshot.log && snapshot.log.last_cycle ? snapshot.log.last_cycle : "No completed cycle found in recent log tail.");
      text("log", snapshot.log && snapshot.log.tail ? snapshot.log.tail.join("\\n") : "");
    }}

    async function refresh() {{
      try {{
        const response = await fetch("/api/status", {{ cache: "no-store" }});
        render(await response.json());
      }} catch (error) {{
        render({{ overall: "down", message: "Status page could not refresh data", generated_at: new Date().toLocaleString(), actions: ["./control.sh dashboard-logs"] }});
      }}
    }}

    document.getElementById("refresh").addEventListener("click", refresh);
    render(JSON.parse(document.getElementById("initial-data").textContent));
    window.setInterval(refresh, refreshSeconds * 1000);
  </script>
</body>
</html>
"""


class StatusHandler(BaseHTTPRequestHandler):
    stale_minutes = DEFAULT_STALE_MINUTES
    refresh_seconds = 30
    log_path = None

    def _snapshot(self) -> dict:
        return build_snapshot(log_path=self.log_path, stale_minutes=self.stale_minutes)

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        snapshot = self._snapshot()

        if path == "/":
            body = html_page(snapshot, self.refresh_seconds).encode("utf-8")
            self._send_bytes(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/status":
            body = json.dumps(snapshot, indent=2, default=_json_default).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", body)
            return
        if path == "/healthz":
            ok = snapshot.get("overall") not in {"down"}
            body = (snapshot.get("message", "") + "\n").encode("utf-8")
            self._send_bytes(200 if ok else 503, "text/plain; charset=utf-8", body)
            return

        self._send_bytes(404, "text/plain; charset=utf-8", b"Not found\n")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("status_page: " + fmt % args + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local MasterBall status page.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host. Defaults to local-only 127.0.0.1.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES, help="Minutes without log activity before status is down.")
    parser.add_argument("--refresh-seconds", type=int, default=30, help="Browser auto-refresh interval.")
    parser.add_argument("--log-path", default=None, help="Monitor log path override.")
    parser.add_argument("--json", action="store_true", help="Print one JSON status snapshot and exit.")
    args = parser.parse_args()

    if args.json:
        print(json.dumps(build_snapshot(log_path=args.log_path, stale_minutes=args.stale_minutes), indent=2, default=_json_default))
        return 0

    StatusHandler.stale_minutes = args.stale_minutes
    StatusHandler.refresh_seconds = args.refresh_seconds
    StatusHandler.log_path = args.log_path

    server = ThreadingHTTPServer((args.host, args.port), StatusHandler)
    print(f"MasterBall status page: http://{args.host}:{args.port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping status page.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
