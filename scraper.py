"""Scraper sports.ws -> data/free_agents.json, data/free_agents.csv, data/players_all.json

Secrets:   SPORTSWS_USER (username sports.ws), SPORTSWS_PASS
Variables (opzionali):
  PLAYERS_URL     default https://sports.ws/nba/stats   ({page} se la paginazione e' nell'URL)
  LOGIN_URL       default https://sports.ws/fantasy-basketball/
  USER_SELECTOR   default campo con placeholder "Username"
  PASS_SELECTOR   default input[type=password]
  MAX_PAGES       default 40
  LEAGUE_URL      default https://sports.ws/fantasy-basketball/league/ (imposta la lega NBA attiva)
Liberi = righe con "-" nella colonna Team.
"""
import csv, datetime, hashlib, json, os, sys
from playwright.sync_api import sync_playwright

USER = os.environ["SPORTSWS_USER"]
PWD = os.environ["SPORTSWS_PASS"]
PLAYERS_URL = os.environ.get("PLAYERS_URL") or "https://sports.ws/nba/stats"
LOGIN_URL = os.environ.get("LOGIN_URL") or "https://sports.ws/fantasy-basketball/"
USER_SEL = os.environ.get("USER_SELECTOR") or (
    "input[placeholder='Username' i], input[name*=user i], input[id*=user i], input[type=email]")
PASS_SEL = os.environ.get("PASS_SELECTOR") or "input[type=password]"
MAX_PAGES = int(os.environ.get("MAX_PAGES") or 40)
LEAGUE_URL = os.environ.get("LEAGUE_URL") or "https://sports.ws/fantasy-basketball/league/"
OUT, DEBUG = "data", "debug"
DROP_COLS = {"Action", "Watch"}
NAV_TIMEOUT = 60_000

# Il sito ha pubblicita' e tracker che non smettono mai di caricare:
# niente "networkidle", e blocchiamo le risorse inutili per andare piu' veloci.
BLOCK_TYPES = {"image", "media", "font"}
BLOCK_HOSTS = ("doubleclick", "googlesyndication", "googletagmanager", "google-analytics",
               "adservice", "amazon-adsystem", "adnxs", "criteo", "taboola", "outbrain",
               "facebook.net", "quantserve", "scorecardresearch", "pubmatic", "rubicon",
               "openx", "moatads", "consensu", "cookielaw", "onetrust", "privacy-mgmt")

JS_TABLE = """() => {
  const t = [...document.querySelectorAll('table')].sort((a,b) => b.rows.length - a.rows.length)[0];
  if (!t) return null;
  return [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim()));
}"""

JS_FIRST_ROW = """() => {
  const t = [...document.querySelectorAll('table')].sort((a,b) => b.rows.length - a.rows.length)[0];
  return t && t.rows[1] ? t.rows[1].innerText : '';
}"""

JS_MAX_LENGTH = """() => {
  for (const s of document.querySelectorAll('select')) {
    const nums = [...s.options].map(o => parseInt(o.value)).filter(n => !isNaN(n));
    if (nums.length >= 2 && nums.some(n => n >= 50)) {
      const best = [...s.options].reduce((a, o) =>
        (parseInt(o.value) === -1 || parseInt(o.value) > parseInt(a.value)) ? o : a);
      if (s.value === best.value) return null;
      s.value = best.value; s.dispatchEvent(new Event('change', {bubbles: true}));
      return best.value;
    }
  }
  return null;
}"""

JS_LOGIN = """([u, p, us, ps]) => {
  const user = document.querySelector(us), pw = document.querySelector(ps);
  if (!user || !pw) return 'campi non trovati';
  const set = (el, v) => { el.value = v;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true})); };
  set(user, u); set(pw, p);
  const scope = pw.closest('form') || document;
  const btn = scope.querySelector('button[type=submit], input[type=submit]') ||
    [...scope.querySelectorAll('button, a, input[type=button]')]
      .find(b => /log\\s*in/i.test(b.innerText || b.value || ''));
  if (btn) { btn.click(); return 'click'; }
  if (pw.form) { pw.form.submit(); return 'submit'; }
  return 'pulsante non trovato';
}"""

JS_NEXT = """() => {
  const cands = [...document.querySelectorAll('a, button'), ...document.querySelectorAll('li, span')].filter(e => {
    const t = (e.innerText || '').trim();
    return /^(next|›|»|>|>>)$/i.test(t) || /next/i.test(e.getAttribute('aria-label') || '');
  });
  const el = cands.find(e => !e.disabled && !/disabled/.test(e.className + ' ' + (e.parentElement||{}).className));
  if (!el) return false;
  el.click(); return true;
}"""


JS_INFO = """() => {
  const t = [...document.querySelectorAll('table')].sort((a,b) => b.rows.length - a.rows.length)[0];
  let box = t; for (let i = 0; i < 4 && box && box.parentElement; i++) box = box.parentElement;
  box = box || document.body;
  const ctl = [...box.querySelectorAll('a, button, select, input, li')].filter(e => !t || !t.contains(e))
    .slice(0, 120).map(e => ({tag: e.tagName, text: (e.innerText || e.value || '').trim().slice(0, 40),
      cls: String(e.className || '').slice(0, 60), href: e.getAttribute('href'),
      onclick: (e.getAttribute('onclick') || '').slice(0, 100),
      opts: e.tagName === 'SELECT' ? [...e.options].map(o => o.value + ':' + o.text) : undefined}));
  return {url: location.href, title: document.title, table_rows: t ? t.rows.length : 0,
          headings: [...document.querySelectorAll('h1,h2,h3')].map(h => h.innerText.trim()).slice(0, 10), controls: ctl};
}"""


def goto(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)


def login(page):
    goto(page, LOGIN_URL)
    page.wait_for_selector(PASS_SEL, state="attached", timeout=30_000)
    res = page.evaluate(JS_LOGIN, [USER, PWD, USER_SEL, PASS_SEL])
    print("Login:", res)
    if res not in ("click", "submit"):
        raise RuntimeError(f"Login fallito: {res}")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except Exception:
        pass
    page.wait_for_timeout(4000)


def wait_table(page):
    page.wait_for_function(
        "() => [...document.querySelectorAll('table')].some(t => t.rows.length > 5)",
        timeout=45_000)


def wait_change(page, before):
    try:
        page.wait_for_function(f"() => ({JS_FIRST_ROW})() !== {json.dumps(before)}", timeout=20_000)
    except Exception:
        pass
    page.wait_for_timeout(500)


def scrape(page):
    paged = "{page}" in PLAYERS_URL
    header, rows, seen = None, [], set()
    for i in range(1, MAX_PAGES + 1):
        if paged or i == 1:
            goto(page, PLAYERS_URL.format(page=i) if paged else PLAYERS_URL)
            wait_table(page)
            if i == 1:
                info = page.evaluate(JS_INFO)
                with open(os.path.join(OUT, "page_info.json"), "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=1)
                print("Pagina statistiche:", info["url"], "|", info["title"])
                before = page.evaluate(JS_FIRST_ROW)
                n = page.evaluate(JS_MAX_LENGTH)
                if n:
                    print("Righe per pagina impostate a", n)
                    page.wait_for_timeout(3000)
        table = page.evaluate(JS_TABLE)
        if not table or len(table) < 2:
            break
        head, body = table[0], table[1:]
        header = header or head
        new = 0
        for r in body:
            key = "|".join(r)
            if r == head or key in seen or len(r) != len(header):
                continue
            seen.add(key)
            rows.append(r)
            new += 1
        print(f"Pagina {i}: {new} nuove righe (totale {len(rows)})")
        if new == 0:
            break
        if not paged:
            before = page.evaluate(JS_FIRST_ROW)
            if not page.evaluate(JS_NEXT):
                break
            wait_change(page, before)
    return header, rows


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000},
                                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36")
        ctx.set_default_timeout(30_000)
        ctx.route("**/*", lambda r: r.abort() if (r.request.resource_type in BLOCK_TYPES or
                  any(h in r.request.url for h in BLOCK_HOSTS)) else r.continue_())
        page = ctx.new_page()
        try:
            login(page)
            if LEAGUE_URL:
                goto(page, LEAGUE_URL)
                page.wait_for_timeout(3000)
                print("Contesto lega:", page.url)
            header, rows = scrape(page)
        except Exception:
            os.makedirs(DEBUG, exist_ok=True)
            print("URL al momento dell'errore:", page.url)
            try:
                page.screenshot(path=f"{DEBUG}/screenshot.png", full_page=True)
            except Exception as e:
                print("Screenshot non riuscito:", e)
            raise
        finally:
            browser.close()

    if not rows:
        sys.exit("Nessuna riga trovata nella tabella")
    if "Team" not in header:
        sys.exit(f"Colonna Team non trovata (login non riuscito?). Colonne: {header}")

    digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    hash_file = os.path.join(OUT, ".hash")
    if os.path.exists(hash_file) and open(hash_file).read().strip() == digest:
        print("Nessuna variazione, niente da salvare")
        return

    cols = [c for c in header if c not in DROP_COLS]
    players = [{c: v for c, v in zip(header, r) if c not in DROP_COLS} for r in rows]
    free = [pl for pl in players if pl.get("Team", "").strip() == "-"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    for name, data in (("players_all.json", players), ("free_agents.json", free)):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            json.dump({"updated": now, "count": len(data), "columns": cols, "players": data},
                      f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "free_agents.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(free)
    open(hash_file, "w").write(digest)
    print(f"Salvati {len(players)} giocatori, di cui {len(free)} liberi")


if __name__ == "__main__":
    main()
