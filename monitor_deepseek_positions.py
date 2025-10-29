"""
Usage:

  pip install playwright plyer
  playwright install
  python monitor_deepseek_positions.py

Description:
- Opens https://nof1.ai/models/deepseek-chat-v3.1 with Playwright (Python, async, headed Chromium)
- Waits for network idle and extra delay, disables cache via headers
- Extracts the ACTIVE POSITIONS section text
- Polls every 10s, detects changes; on change:
  - prints alert and brief summary (Unrealized P&L change if available)
  - appends JSON line to positions-log.txt
  - saves screenshot into positions_snapshots/positions_<timestamp>.png
  - sends desktop notification (plyer)
- On load failure, waits 30s and retries
- Prints heartbeat "no change" when content unchanged
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from playwright.async_api import async_playwright, Page
import argparse
import sys

# Optional dependencies
try:
    from plyer import notification
    PLYER_AVAILABLE = True
except Exception:
    PLYER_AVAILABLE = False

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    RICH_AVAILABLE = True
except Exception:
    RICH_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
    TKINTER_AVAILABLE = True
except Exception:
    TKINTER_AVAILABLE = False


MODEL_DISPLAY_NAME = "DEEPSEEK CHAT V3.1"
TARGET_URL = "https://nof1.ai/models/deepseek-chat-v3.1"

# Use script directory as base path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
LOG_PATH = os.path.join(SCRIPT_DIR, "positions-log.txt")
SNAP_DIR = os.path.join(SCRIPT_DIR, "positions_snapshots")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def wait_loaded(page: Page) -> None:
    await page.wait_for_load_state("networkidle", timeout=60000)
    await page.wait_for_timeout(4000)


async def find_active_positions_container(page: Page) -> Tuple[Optional[str], Optional[str]]:
    """
    Locate the ACTIVE POSITIONS section using text-based heuristics and return:
    - full inner_text of the nearest substantial container
    - a selector string that can be used for taking a screenshot of the section (if available)
    """
    # First try: find the exact/nearby node by text, then choose a meaningful container relative to it
    locator = page.get_by_text("ACTIVE POSITIONS", exact=False)
    try:
        await locator.first.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    # Evaluate DOM to pick a container below the header
    script = r"""
    () => {
      function getText(el){ return (el && el.innerText || '').trim(); }
      const headerCandidates = Array.from(document.querySelectorAll('*')).filter(el => {
        const t = (el.textContent || '').toUpperCase();
        return t.includes('ACTIVE POSITIONS');
      });
      if (!headerCandidates.length) return { text: '', selector: '' };
      // Prefer the smallest height header-like node
      headerCandidates.sort((a,b)=>a.getBoundingClientRect().height - b.getBoundingClientRect().height);
      const header = headerCandidates[0];

      // Try: next siblings with substantial text
      let node = header.nextElementSibling;
      while (node && getText(node).length < 40) node = node.nextElementSibling;
      if (node && getText(node)) {
        node.setAttribute('data-active-positions','1');
        return { text: getText(node), selector: '[data-active-positions="1"]' };
      }

      // Try: ancestor then next sibling
      let parent = header.parentElement;
      while (parent) {
        let sib = parent.nextElementSibling;
        while (sib && getText(sib).length < 40) sib = sib.nextElementSibling;
        if (sib && getText(sib)) {
          sib.setAttribute('data-active-positions','1');
          return { text: getText(sib), selector: '[data-active-positions="1"]' };
        }
        parent = parent.parentElement;
      }

      // Fallback: capture a moderately large container around header
      const container = header.closest('section,article,div') || header;
      container.setAttribute('data-active-positions','1');
      return { text: getText(container), selector: '[data-active-positions="1"]' };
    }
    """
    try:
        res = await page.evaluate(script)
        text = (res or {}).get("text") or ""
        selector = (res or {}).get("selector") or ""
        return text, (selector or None)
    except Exception:
        try:
            body_text = await page.inner_text("body")
            return body_text, None
        except Exception:
            return "", None


async def save_section_screenshot(page: Page, selector: Optional[str], path: str) -> None:
    try:
        if selector:
            await page.locator(selector).screenshot(path=path)
        else:
            await page.screenshot(path=path, full_page=True)
    except Exception:
        try:
            await page.screenshot(path=path, full_page=True)
        except Exception:
            pass


def extract_unrealized_pnl(text: str) -> Optional[float]:
    """Attempt to extract Unrealized P&L numeric value from text.
    Supports patterns like: Unrealized P&L: $1,234.56 or Unrealized: -$123.45
    Returns a float (can be negative) if found.
    """
    if not text:
        return None
    # Common patterns
    patterns = [
        r"UNREALIZED[^\n$]*\$\s*([0-9,]+(?:\.[0-9]+)?)",
        r"UNREALISED[^\n$]*\$\s*([0-9,]+(?:\.[0-9]+)?)",
        r"UNREALIZED[^\n$]*([-+]?\$\s*[0-9,]+(?:\.[0-9]+)?)",
    ]
    up = text.upper()
    for pat in patterns:
        m = re.search(pat, up)
        if m:
            val = m.group(1)
            val = val.replace("$", "").replace(",", "").strip()
            try:
                return float(val)
            except Exception:
                continue
    return None


def play_sound():
    """Play system sound notification."""
    try:
        import platform
        system = platform.system()
        if system == "Darwin":  # macOS
            os.system("afplay /System/Library/Sounds/Glass.aiff")
        elif system == "Linux":
            os.system("aplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null || beep")
        elif system == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            # Fallback: terminal bell
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        # Fallback: terminal bell
        sys.stdout.write("\a")
        sys.stdout.flush()


def show_popup(title: str, message: str, details: str = ""):
    """Show popup dialog with position details in background thread."""
    if not TKINTER_AVAILABLE:
        return
    import threading
    
    def _show():
        try:
            root = tk.Tk()
            root.withdraw()  # Hide main window
            
            # Create dialog window
            dialog = tk.Toplevel(root)
            dialog.title(title)
            dialog.geometry("650x450")
            dialog.transient(root)
            dialog.grab_set()
            
            # Header
            tk.Label(dialog, text=message, font=("Arial", 12, "bold"), padx=10, pady=10).pack()
            
            # Details area
            if details:
                text_area = scrolledtext.ScrolledText(dialog, wrap=tk.WORD, width=75, height=18, padx=10, pady=10)
                text_area.insert("1.0", details)
                text_area.config(state=tk.DISABLED)
                text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
            
            # Close button
            tk.Button(dialog, text="关闭 / Close", command=root.quit, padx=20, pady=5).pack(pady=10)
            
            # Center window
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
            y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
            dialog.geometry(f"+{x}+{y}")
            
            dialog.mainloop()
            root.destroy()
        except Exception:
            pass
    
    # Run in background thread to avoid blocking
    thread = threading.Thread(target=_show, daemon=True)
    thread.start()


def parse_positions(text: str):
    """Parse positions from ACTIVE POSITIONS text using structured fields.
    Returns a list of dicts: {symbol, side, leverage, pnl_value, pnl_text, entry_price}
    """
    if not text:
        return []
    # Split by "Entry Time:" pattern to identify individual positions
    position_blocks = re.split(r"Entry Time:\s*\d+:\d+:\d+", text, flags=re.IGNORECASE)
    symbols_pattern = r"BTC|ETH|SOL|XRP|BNB|DOGE|ADA|AVAX|TON|LTC|DOT|LINK|ATOM|APE|NEAR|OP|ARB|FTM|SUI|SEI|PEPE|SHIB|XLM|ETC|BCH|APT|TIA|INJ|RUNE|UNI|MATIC|POL|WIF|ORDI"
    results = []
    
    for block in position_blocks[1:]:  # Skip first empty block
        block_upper = block.upper()
        
        # Extract fields using structured patterns
        side_m = re.search(r"Side:\s*(LONG|SHORT)", block_upper)
        entry_price_m = re.search(r"Entry Price:\s*\$?([0-9,]+(?:\.[0-9]+)?)", block_upper)
        leverage_m = re.search(r"Leverage:\s*(\d+)\s*X", block_upper)
        pnl_m = re.search(r"Unrealized P&L:\s*([+-]?\$\s*[0-9,]+(?:\.[0-9]+)?)", block_upper, re.IGNORECASE)
        quantity_m = re.search(r"Quantity:\s*([0-9,]+(?:\.[0-9]+)?)", block_upper)
        
        # Try to find explicit symbol first (most reliable)
        sym_m = re.search(rf"\b({symbols_pattern})\b", block_upper)
        symbol = sym_m.group(1) if sym_m else ""
        
        # If no explicit symbol found, try to infer from entry price (heuristic)
        # Note: This is less reliable as prices can overlap across different assets
        if not symbol and entry_price_m:
            price = float(entry_price_m.group(1).replace(",", ""))
            # Very rough heuristics based on typical price ranges (not guaranteed accurate)
            if price < 0.5:
                # Low price assets: some altcoins, not reliable
                pass
            elif 0.5 <= price < 10:
                # Medium-low: could be SOL, some altcoins
                symbol = "SOL"  # Common but not definitive
            elif 1000 <= price < 5000:
                # ETH range (but could overlap with others)
                symbol = "ETH"
            elif 50000 <= price < 150000:
                # BTC range
                symbol = "BTC"
        
        side = side_m.group(1).title() if side_m else ""
        leverage = (leverage_m.group(1) + "X") if leverage_m else ""
        pnl_text = pnl_m.group(1).replace(" ", "").strip() if pnl_m else ""
        entry_price = entry_price_m.group(1) if entry_price_m else ""
        
        pnl_value = None
        if pnl_text:
            try:
                pnl_value = float(pnl_text.replace("$", "").replace(",", "").replace("+", ""))
            except Exception:
                pnl_value = None
        
        if pnl_m or side_m:  # At least have P&L or side to consider valid
            results.append({
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
                "pnl_value": pnl_value,
                "pnl_text": pnl_text,
                "entry_price": entry_price,
            })
    
    # Sort by pnl_value desc (None last)
    results.sort(key=lambda r: (r["pnl_value"] is None, -(r["pnl_value"] or 0.0)))
    return results


def render_positions_table(positions):
    """Render positions in a rich table with color coding."""
    if not positions:
        return
    
    console = Console()
    table = Table(show_header=True, header_style="bold", title="Active Positions")
    table.add_column("Symbol", style="bold cyan", justify="center")
    table.add_column("Side", justify="center")
    table.add_column("Leverage", justify="center")
    table.add_column("Entry Price", justify="right", style="dim")
    table.add_column("Unrealized P&L", justify="right")
    
    total_pnl = 0.0
    for row in positions:
        pnl = row.get("pnl_value")
        pnl_text = row.get("pnl_text") or "N/A"
        entry_price = row.get("entry_price", "")
        if entry_price:
            entry_price = f"${entry_price}"
        
        # Color coding: green for profit, red for loss
        pnl_style = "green" if (pnl is not None and pnl >= 0) else ("red" if pnl is not None else "")
        table.add_row(
            row.get("symbol", "?") or "?",
            row.get("side", ""),
            row.get("leverage", ""),
            entry_price,
            f"[{pnl_style}]{pnl_text}[/{pnl_style}]" if pnl_style else pnl_text,
        )
        if pnl is not None:
            total_pnl += pnl
    
    # Add total row if we have P&L data
    if any(p.get("pnl_value") is not None for p in positions):
        total_style = "green" if total_pnl >= 0 else "red"
        table.add_section()
        table.add_row(
            "[bold]TOTAL[/bold]",
            "",
            "",
            "",
            f"[bold {total_style}]{total_pnl:+.2f}[/bold {total_style}]",
        )
    
    console.print()
    console.print(Panel.fit("⚡ DeepSeek Positions Updated", style="bold cyan"))
    console.print()
    console.print(table)
    console.print()


def escape_html(text):
    """Escape HTML special characters."""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;"))


def write_positions_html(positions, path):
    """Generate HTML file with positions table."""
    try:
        import html
        
        total_pnl = sum(p.get("pnl_value") or 0.0 for p in positions)
        
        rows_html = []
        for p in positions:
            symbol = escape_html(p.get("symbol", "?") or "?")
            side = escape_html(p.get("side", ""))
            leverage = escape_html(p.get("leverage", ""))
            entry_price = escape_html(p.get("entry_price", ""))
            pnl_text = escape_html(p.get("pnl_text", "N/A"))
            pnl_value = p.get("pnl_value")
            pnl_class = "profit" if (pnl_value is not None and pnl_value >= 0) else ("loss" if pnl_value is not None else "")
            
            entry_display = f"${entry_price}" if entry_price else ""
            rows_html.append(
                f"<tr><td>{symbol}</td><td>{side}</td><td>{leverage}</td>"
                f"<td>{entry_display}</td><td class='{pnl_class}'>{pnl_text}</td></tr>"
            )
        
        rows_html_str = "".join(rows_html)
        
        html_content = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>DeepSeek Positions</title>
<style>
body {{
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 900px;
    margin: 20px auto;
    padding: 20px;
}}
h2 {{ color: #333; }}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 20px 0;
}}
td, th {{
    border: 1px solid #ddd;
    padding: 8px 12px;
    text-align: left;
}}
th {{
    background: #f5f5f5;
    font-weight: bold;
}}
.profit {{ color: #28a745; font-weight: bold; }}
.loss {{ color: #dc3545; font-weight: bold; }}
.totals-row {{
    background: #f9f9f9;
    font-weight: bold;
}}
.totals-row .profit {{ font-size: 1.1em; }}
.totals-row .loss {{ font-size: 1.1em; }}
</style>
</head><body>
<h2>DeepSeek Active Positions</h2>
<p><small>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small></p>
<table>
<thead>
<tr><th>Symbol</th><th>Side</th><th>Leverage</th><th>Entry Price</th><th>Unrealized P&L</th></tr>
</thead>
<tbody>
{rows_html_str}
</tbody>
</table>
<p><strong>Total P&L:</strong> <span class="{'profit' if total_pnl >= 0 else 'loss'}">${total_pnl:+.2f}</span></p>
</body></html>
"""
        
        html_path = os.path.join(SCRIPT_DIR, "positions_latest.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception:
        pass


async def run_monitor() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        await context.set_extra_http_headers({
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })

        # Ensure snapshot dir
        try:
            os.makedirs(SNAP_DIR, exist_ok=True)
        except Exception:
            pass

        page = await context.new_page()
        last_text: Optional[str] = None
        last_unrealized: Optional[float] = None

        # Initial navigation with retry policy
        while True:
            try:
                await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
                await wait_loaded(page)
                break
            except Exception:
                print("Initial load failed, retrying in 30s...")
                await asyncio.sleep(30)

        # Print startup information
        print("=" * 60)
        print(f"🚀 DeepSeek Positions Monitor Started")
        print("=" * 60)
        print(f"Target URL: {TARGET_URL}")
        print(f"Log file: {LOG_PATH}")
        print(f"Screenshots: {SNAP_DIR}")
        print(f"Visual mode: {'✓ Enabled' if ARGS.visual else '✗ Disabled'}")
        alerts_enabled = []
        if ARGS.notify:
            alerts_enabled.append("Desktop Notification" + (" ✓" if PLYER_AVAILABLE else " (⚠ plyer not installed)"))
        if ARGS.sound:
            alerts_enabled.append("Sound Alert ✓")
        if ARGS.popup:
            alerts_enabled.append("Popup Window" + (" ✓" if TKINTER_AVAILABLE else " (⚠ tkinter not available)"))
        if alerts_enabled:
            print(f"Alerts: {', '.join(alerts_enabled)}")
        else:
            print("Alerts: None (using default --notify)")
        print("=" * 60)
        print()
        
        # Initial scrape
        text, selector = await find_active_positions_container(page)
        if not text.strip():
            debug_path = os.path.join(SCRIPT_DIR, "debug_positions_full.png")
            await page.screenshot(path=debug_path, full_page=True)
            print(f"⚠ No ACTIVE POSITIONS content detected on first load; saved {debug_path}")

        last_text = text
        last_unrealized = extract_unrealized_pnl(text or "")
        initial_payload = {
            "timestamp": utc_now_iso(),
            "model": MODEL_DISPLAY_NAME,
            "active_positions": (text or "").strip(),
        }
        print(json.dumps(initial_payload, ensure_ascii=False))
        # Save initial snapshot
        ts_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        await save_section_screenshot(page, selector, f"{SNAP_DIR}/positions_{ts_name}.png")

        # Monitoring loop
        try:
            while True:
                await asyncio.sleep(10)
                # Reload with retry policy
                try:
                    await page.reload(wait_until="networkidle", timeout=60000)
                    await wait_loaded(page)
                except Exception:
                    print("Reload failed, retrying in 30s...")
                    await asyncio.sleep(30)
                    try:
                        await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
                        await wait_loaded(page)
                    except Exception:
                        # Give up this cycle
                        print("Navigation retry failed; skipping this cycle.")
                        continue

                text, selector = await find_active_positions_container(page)
                ts_now = utc_now_iso()

                current = (text or "").strip()
                previous = (last_text or "").strip() if last_text is not None else None

                if previous is None:
                    print("Initialized.")
                elif current != previous:
                    # Change detected
                    curr_unreal = extract_unrealized_pnl(current)
                    delta_msg = ""
                    if last_unrealized is not None and curr_unreal is not None:
                        delta = curr_unreal - last_unrealized
                        delta_msg = f" (Δ Unrealized P&L: {delta:+.2f})"

                    print(f"⚡ Positions updated!{delta_msg}")
                    payload = {
                        "timestamp": ts_now,
                        "model": MODEL_DISPLAY_NAME,
                        "active_positions": current,
                    }
                    print(json.dumps(payload, ensure_ascii=False))

                    # Visualize in terminal if enabled
                    if ARGS.visual:
                        if not RICH_AVAILABLE:
                            print("[visual] rich not installed; run: pip install rich")
                        else:
                            positions = parse_positions(current)
                            if positions:
                                render_positions_table(positions)

                    # Log
                    try:
                        with open(LOG_PATH, "a", encoding="utf-8") as f:
                            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    except Exception:
                        pass

                    # Snapshot
                    ts_name = datetime.now().strftime("%Y%m%d_%H%M%S")
                    await save_section_screenshot(page, selector, f"{SNAP_DIR}/positions_{ts_name}.png")

                    # Optional HTML export (latest)
                    try:
                        positions = parse_positions(current)
                        write_positions_html(positions, os.path.join(SCRIPT_DIR, "positions_latest.html"))
                    except Exception:
                        pass

                    # Alert based on user preferences
                    alert_title = "DeepSeek Positions Updated"
                    alert_msg = delta_msg.strip() or "Active positions changed"
                    
                    if ARGS.notify:
                        if PLYER_AVAILABLE:
                            try:
                                notification.notify(
                                    title=alert_title,
                                    message=alert_msg,
                                    timeout=5,
                                )
                            except Exception:
                                pass
                        else:
                            print("[提醒] plyer 未安装，桌面通知不可用。运行: pip install plyer")
                    
                    if ARGS.sound:
                        play_sound()
                    
                    if ARGS.popup:
                        if TKINTER_AVAILABLE:
                            # Format positions details for popup
                            positions = parse_positions(current)
                            details_lines = ["当前持仓详情 / Current Positions:\n"]
                            if positions:
                                total = sum(p.get("pnl_value") or 0.0 for p in positions)
                                details_lines.append(
                                    f"{'='*50}\n"
                                    f"{'Symbol':<10} {'Side':<8} {'Leverage':<10} {'Entry Price':<15} {'P&L':<15}\n"
                                    f"{'='*50}"
                                )
                                for pos in positions:
                                    symbol = pos.get("symbol", "?") or "?"
                                    side = pos.get("side", "")
                                    leverage = pos.get("leverage", "")
                                    entry_price = pos.get("entry_price", "")
                                    entry_display = f"${entry_price}" if entry_price else "N/A"
                                    pnl_text = pos.get("pnl_text", "N/A")
                                    details_lines.append(
                                        f"{symbol:<10} {side:<8} {leverage:<10} {entry_display:<15} {pnl_text:<15}"
                                    )
                                details_lines.append(f"{'='*50}\nTotal P&L: ${total:+.2f}")
                            else:
                                details_lines.append("未能解析到持仓信息 / Unable to parse positions")
                            details_text = "\n".join(details_lines)
                            show_popup(alert_title, alert_msg, details_text)
                        else:
                            print("[提醒] tkinter 不可用，弹窗详情不可用。")

                    last_text = current
                    last_unrealized = curr_unreal if curr_unreal is not None else last_unrealized
                else:
                    print("no change")
        except KeyboardInterrupt:
            print("Stopped by user.")
        finally:
            await context.close()
            await browser.close()


async def main() -> None:
    global ARGS
    parser = argparse.ArgumentParser(
        description="Monitor DeepSeek Chat V3.1 Active Positions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
提醒方式选择 / Alert Options (可组合使用):
  --notify   桌面通知 / Desktop notification (需要 plyer)
  --sound    声音提醒 / Sound alert
  --popup    弹窗详情 / Popup window with details (需要 tkinter)

示例 / Examples:
  python monitor_deepseek_positions.py --visual --notify --sound
  python monitor_deepseek_positions.py --popup --sound
        """
    )
    parser.add_argument("--visual", action="store_true", help="启用终端彩色表格 / Enable rich table visualization")
    parser.add_argument("--notify", action="store_true", help="启用桌面通知 / Enable desktop notification")
    parser.add_argument("--sound", action="store_true", help="启用声音提醒 / Enable sound alert")
    parser.add_argument("--popup", action="store_true", help="启用弹窗详情 / Enable popup window")
    ARGS = parser.parse_args()
    
    # If no alert method specified, use notify by default for backward compatibility
    if not (ARGS.notify or ARGS.sound or ARGS.popup):
        ARGS.notify = True
    
    await run_monitor()


if __name__ == "__main__":
    asyncio.run(main())


