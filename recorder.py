"""
Session Recorder — disposable one-shot data collection tool.

Launches a real Chromium browser via Playwright, persists cookies between runs,
and captures EVERYTHING that happens during a manual generation session:

  - All click / input / change / keydown / submit events
    with multi-strategy element selectors (testid, aria, role, text, classes, xpath)
  - All network requests and responses (method, URL, headers, bodies)
  - All WebSocket frames (sent and received)
  - Screenshots after each click
  - Final cookies and localStorage snapshot

Usage:  python recorder.py
Stop:   close the browser window (recording stops automatically)

Output: ./recordings/session_<timestamp>/
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Page,
    Request,
    Response,
    WebSocket,
    async_playwright,
)
from rich.console import Console
from rich.panel import Panel

console = Console()


EVENT_HOOK_JS = r"""
(() => {
  if (window.__recorderInstalled) return;
  window.__recorderInstalled = true;

  function selectorsFor(el) {
    if (!el || el.nodeType !== 1) return {};
    const sel = { tag: el.tagName.toLowerCase() };
    if (el.id) sel.id = el.id;
    const testid = el.getAttribute('data-testid')
                || el.getAttribute('data-test-id')
                || el.getAttribute('data-test');
    if (testid) sel.testid = testid;
    const aria = el.getAttribute('aria-label');
    if (aria) sel.aria = aria;
    const role = el.getAttribute('role');
    if (role) sel.role = role;
    const name = el.getAttribute('name');
    if (name) sel.name = name;
    const ph = el.getAttribute('placeholder');
    if (ph) sel.placeholder = ph;
    const type = el.getAttribute('type');
    if (type) sel.type = type;
    const text = (el.textContent || '').trim().slice(0, 120);
    if (text) sel.text = text;
    if (el.classList && el.classList.length) {
      sel.classes = Array.from(el.classList).slice(0, 8);
    }
    // XPath
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && path.length < 25) {
      let idx = 1;
      let sib = cur.previousElementSibling;
      while (sib) {
        if (sib.tagName === cur.tagName) idx++;
        sib = sib.previousElementSibling;
      }
      path.unshift(cur.tagName.toLowerCase() + '[' + idx + ']');
      cur = cur.parentElement;
    }
    sel.xpath = '/html/body/' + path.join('/');
    return sel;
  }

  function emit(type, data) {
    try {
      window.__recorderEmit(JSON.stringify({ type, ts: Date.now(), ...data }));
    } catch (e) {}
  }

  document.addEventListener('click', (e) => {
    emit('click', {
      selectors: selectorsFor(e.target),
      x: e.clientX, y: e.clientY,
      url: location.href,
    });
  }, true);

  document.addEventListener('input', (e) => {
    emit('input', {
      selectors: selectorsFor(e.target),
      value: typeof e.target.value === 'string' ? e.target.value.slice(0, 5000) : null,
      url: location.href,
    });
  }, true);

  document.addEventListener('change', (e) => {
    emit('change', {
      selectors: selectorsFor(e.target),
      value: typeof e.target.value === 'string' ? e.target.value.slice(0, 1000) : null,
      url: location.href,
    });
  }, true);

  document.addEventListener('keydown', (e) => {
    if (['Enter', 'Tab', 'Escape'].includes(e.key) || e.ctrlKey || e.metaKey) {
      emit('keydown', { key: e.key, ctrl: e.ctrlKey, meta: e.metaKey, url: location.href });
    }
  }, true);

  document.addEventListener('submit', (e) => {
    emit('submit', { selectors: selectorsFor(e.target), url: location.href });
  }, true);

  document.addEventListener('dragover', (e) => { e.preventDefault(); }, true);
  document.addEventListener('drop', (e) => {
    const files = (e.dataTransfer && e.dataTransfer.files) ? Array.from(e.dataTransfer.files) : [];
    emit('drop', {
      selectors: selectorsFor(e.target),
      files: files.map(f => ({ name: f.name, size: f.size, type: f.type })),
      url: location.href,
    });
  }, true);
})();
"""


class Recorder:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.shots_dir = session_dir / "screenshots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)

        self.timeline_f = (session_dir / "timeline.jsonl").open("a", encoding="utf-8")
        self.network_f = (session_dir / "network.jsonl").open("a", encoding="utf-8")
        self.ws_f = (session_dir / "websockets.jsonl").open("a", encoding="utf-8")

        self.event_count = 0
        self.network_count = 0
        self.ws_count = 0
        self.shot_count = 0
        self._exposed_pages: set[int] = set()

    def _write(self, fp, obj: dict) -> None:
        try:
            fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
            fp.flush()
        except Exception:
            pass

    def log_event(self, obj: dict) -> None:
        self._write(self.timeline_f, obj)
        self.event_count += 1
        self._status()

    def log_network(self, obj: dict) -> None:
        self._write(self.network_f, obj)
        self.network_count += 1
        self._status()

    def log_ws(self, obj: dict) -> None:
        self._write(self.ws_f, obj)
        self.ws_count += 1
        self._status()

    def _status(self) -> None:
        sys.stdout.write(
            f"\r[recorder] events={self.event_count:<4}  "
            f"network={self.network_count:<4}  "
            f"ws={self.ws_count:<4}  "
            f"shots={self.shot_count:<4}  "
        )
        sys.stdout.flush()

    async def attach_page(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._exposed_pages:
            return
        self._exposed_pages.add(page_id)

        async def on_event(payload_str: str) -> None:
            try:
                payload = json.loads(payload_str)
            except Exception:
                return
            self.log_event(payload)
            if payload.get("type") == "click":
                ts = payload.get("ts", int(datetime.now().timestamp() * 1000))
                shot = self.shots_dir / f"click_{ts}.png"
                try:
                    await page.screenshot(path=str(shot), full_page=False, timeout=2500)
                    self.shot_count += 1
                except Exception:
                    pass

        try:
            await page.expose_function("__recorderEmit", on_event)
        except Exception:
            # already exposed for this page
            pass

        async def on_request(req: Request) -> None:
            try:
                body = None
                try:
                    body = req.post_data
                except Exception:
                    body = None
                self.log_network({
                    "event": "request",
                    "ts": datetime.now().isoformat(),
                    "method": req.method,
                    "url": req.url,
                    "resource_type": req.resource_type,
                    "headers": await req.all_headers(),
                    "body": body[:20000] if isinstance(body, str) else None,
                })
            except Exception:
                pass

        async def on_response(resp: Response) -> None:
            try:
                headers = await resp.all_headers()
                ct = headers.get("content-type", "")
                body = None
                if any(x in ct for x in ("json", "text", "javascript", "xml", "x-www-form-urlencoded")):
                    try:
                        body = (await resp.text())[:30000]
                    except Exception:
                        body = None
                self.log_network({
                    "event": "response",
                    "ts": datetime.now().isoformat(),
                    "status": resp.status,
                    "url": resp.url,
                    "headers": headers,
                    "body": body,
                })
            except Exception:
                pass

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        def on_ws(ws: WebSocket) -> None:
            self.log_ws({
                "event": "open",
                "url": ws.url,
                "ts": datetime.now().isoformat(),
            })
            ws.on("framesent", lambda p: self.log_ws({
                "event": "send", "url": ws.url,
                "payload": str(p)[:8000],
                "ts": datetime.now().isoformat(),
            }))
            ws.on("framereceived", lambda p: self.log_ws({
                "event": "recv", "url": ws.url,
                "payload": str(p)[:8000],
                "ts": datetime.now().isoformat(),
            }))
            ws.on("close", lambda: self.log_ws({
                "event": "close", "url": ws.url,
                "ts": datetime.now().isoformat(),
            }))

        page.on("websocket", on_ws)

        page.on("framenavigated", lambda f: self.log_event({
            "type": "navigate",
            "ts": int(datetime.now().timestamp() * 1000),
            "url": f.url,
            "is_main": f == page.main_frame,
        }))

    async def save_final_state(self, context: BrowserContext) -> None:
        try:
            cookies = await context.cookies()
            (self.session_dir / "cookies_final.json").write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            for i, page in enumerate(context.pages):
                ls = await page.evaluate(
                    "() => Object.fromEntries(Object.entries(localStorage))"
                )
                (self.session_dir / f"localstorage_page_{i}.json").write_text(
                    json.dumps(ls, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass

    def close(self) -> None:
        for fp in (self.timeline_f, self.network_f, self.ws_f):
            try:
                fp.close()
            except Exception:
                pass
        meta = {
            "ended_at": datetime.now().isoformat(),
            "events": self.event_count,
            "network": self.network_count,
            "ws": self.ws_count,
            "screenshots": self.shot_count,
        }
        (self.session_dir / "summary.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )


async def main() -> None:
    project_root = Path(__file__).parent.resolve()
    profile_dir = project_root / "browser_profile"
    profile_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = project_root / "recordings" / f"session_{ts}"
    session_dir.mkdir(parents=True, exist_ok=True)

    (session_dir / "metadata.json").write_text(
        json.dumps(
            {
                "started_at": datetime.now().isoformat(),
                "profile_dir": str(profile_dir),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    console.print(
        Panel.fit(
            "[bold cyan]Runway Automation — Session Recorder[/bold cyan]\n"
            f"Session: [yellow]{session_dir.name}[/yellow]\n\n"
            "[white]A real Chromium browser will open.\n"
            "1. Log in to your platform if needed (cookies are saved for next time).\n"
            "2. Perform ONE complete generation manually:\n"
            "   - upload your reference image\n"
            "   - type the prompt\n"
            "   - set duration\n"
            "   - press Generate\n"
            "   - wait for the result\n"
            "   - download the video\n"
            "3. CLOSE the browser window when done.[/white]\n\n"
            "[dim]Recording stops automatically when you close the browser.[/dim]",
            border_style="cyan",
        )
    )

    rec = Recorder(session_dir)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],
        )

        await context.add_init_script(EVENT_HOOK_JS)

        for page in context.pages:
            await rec.attach_page(page)

        async def on_new_page(page: Page) -> None:
            try:
                await rec.attach_page(page)
            except Exception:
                console.print(f"[red]attach_page error:[/red] {traceback.format_exc()}")

        context.on("page", lambda p: asyncio.create_task(on_new_page(p)))

        if not context.pages:
            await context.new_page()

        loop = asyncio.get_running_loop()
        close_future: asyncio.Future = loop.create_future()

        def on_close(*_args) -> None:
            if not close_future.done():
                close_future.set_result(None)

        context.on("close", on_close)

        # Also poll periodically — context.on('close') sometimes doesn't fire
        # if all pages are closed before the context itself.
        async def poll_alive() -> None:
            while not close_future.done():
                await asyncio.sleep(2)
                if not context.pages:
                    on_close()
                    return

        poll_task = asyncio.create_task(poll_alive())

        try:
            await close_future
        finally:
            poll_task.cancel()
            try:
                await rec.save_final_state(context)
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass

    rec.close()

    console.print()
    console.print(
        Panel.fit(
            f"[green]Recording complete.[/green]\n"
            f"Saved to: [yellow]{session_dir}[/yellow]\n\n"
            f"events:      {rec.event_count}\n"
            f"network:     {rec.network_count}\n"
            f"websockets:  {rec.ws_count}\n"
            f"screenshots: {rec.shot_count}",
            border_style="green",
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
