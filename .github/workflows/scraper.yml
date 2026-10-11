"""Scraper giocatori liberi sports.ws -> data/players.json + data/players.csv

Variabili d'ambiente (da GitHub Secrets / Variables):
  SPORTSWS_USER, SPORTSWS_PASS   credenziali (Secrets)
  PLAYERS_URL                    opzionale, default https://sports.ws/nba/stats
                                 Se la paginazione e' nell'URL, metti {page} al posto del numero.
  LOGIN_URL                      opzionale, default https://sports.ws/login
  MAX_PAGES                      opzionale, default 40

Output:
  data/players_all.json   tutti i giocatori (con la colonna Team della lega)
  data/free_agents.json   solo i liberi, cioe' Team == "-"
  data/free_agents.csv    come sopra, in CSV
"""
import csv, datetime, hashlib, json, os, sys
from playwright.sync_api import sync_playwright

USER = os.environ["SPORTSWS_USER"]
PWD = os.environ["SPORTSWS_PASS"]
PLAYERS_URL = os.environ.get("PLAYERS_URL") or "https://sports.ws/nba/stats"
LOGIN_URL = os.environ.get("LOGIN_URL") or "https://sports.ws/login"
MAX_PAGES = int(os.environ.get("MAX_PAGES") or 40)
DROP_COLS = {"Action", "Watch"}
OUT = "data"

# Stessa logica dello script da console: prende la tabella con piu' righe
JS_TABLE = """() => {
  const t = [...document.querySelectorAll('table')].sort((a,b) => b.rows.length - a.rows.length)[0];
  if (!t) return null;
  return [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim()));
}"""

# Se la tabella ha un selettore "mostra N righe", sceglie il valore piu' alto
JS_MAX_LENGTH = """() => {
  for (const s of document.querySelectorAll('select')) {
    const nums = [...s.options].map(o => parseInt(o.value)).filter(n => !isNaN(n));
    if (nums.length >= 2 && nums.some(n => n >= 50)) {
      const best = [...s.options].reduce((a, o) => (parseInt(o.value) === -1 || parseInt(o.value) > parseInt(a.value)) ? o : a);
      s.value = best.value; s.dispatchEvent(new Event('change', {bubbles: true}));
      return best.value;
    }
  }
  return null;
}"""


def login(page):
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.locator("input[type=email], input[name*=user i], input[name*=email i], input[name*=login i]").first.fill(USER)
    pwd = page.locator("input[type=password]").first
    pwd.fill(PWD)
    pwd.press("Enter")
    page.wait_for_load_state("networkidle")
    if page.locator("input[type=password]:visible").count():
        raise RuntimeError("Login fallito: controlla credenziali o LOGIN_URL")


def scrape(page):
    paged = "{page}" in PLAYERS_URL
    header, rows, seen = None, [], set()
    for i in range(1, MAX_PAGES + 1):
        if paged or i == 1:
            page.goto(PLAYERS_URL.format(page=i) if paged else PLAYERS_URL, wait_until="networkidle")
            if page.evaluate(JS_MAX_LENGTH):
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
        table = page.evaluate(JS_TABLE)
        if not table or len(table) < 2:
            break
        head, body = table[0], table[1:]
        header = header or head
        new = 0
        for r in body:
            key = "|".join(r)
            if r == head or key in seen:
                continue
            seen.add(key)
            rows.append(r)
            new += 1
        if new == 0:
            break
        if not paged:  # paginazione via link "Next"
            nxt = page.locator("a:has-text('Next'), button:has-text('Next'), a:has-text('›'), a:has-text('»')").first
            if not nxt.count() or not nxt.is_visible():
                break
            nxt.click()
            page.wait_for_load_state("networkidle")
    return header, rows


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            login(page)
            header, rows = scrape(page)
        except Exception:
            os.makedirs("debug", exist_ok=True)
            page.screenshot(path="debug/screenshot.png", full_page=True)
            raise
        finally:
            browser.close()

    if not rows:
        sys.exit("Nessuna riga trovata: controlla PLAYERS_URL")

    digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    hash_file = os.path.join(OUT, ".hash")
    if os.path.exists(hash_file) and open(hash_file).read().strip() == digest:
        print("Nessuna variazione, niente da salvare")
        return

    cols = [c for c in header if c not in DROP_COLS]
    players = [{c: v for c, v in zip(header, r) if c not in DROP_COLS} for r in rows]
    if "Team" not in cols:
        sys.exit(f"Colonna Team non trovata. Colonne: {header}")
    free = [p for p in players if p.get("Team", "").strip() in ("-", "")]
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
