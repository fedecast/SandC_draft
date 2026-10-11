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
  const t = [...document.querySelectorAll('table')].sort((a,b) => b.rows.length -
