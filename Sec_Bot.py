"""
Telegram Stock Assistant Bot
------------------------------
Send A Stock Name Or Ticker, Get Back: Price, Fundamentals, Quarterly
Results, Dividends, News, And Technical Analysis — For Both US And Indian
(NSE/BSE) Stocks.

Data Sources (With Real, Layered Fallback Chains):
    Ticker Resolution:
        1. Direct Ticker Match (Raw Input, Then .NS, Then .BO)
        2. Yahoo Finance Search API (Resolves Company Names To Tickers)
        3. Financial Modeling Prep Search-Name API (Second Name Resolver)
        4. Stooq Symbol Guess (Last-Resort Resolver For US Tickers)

    Price Data:
        1. Yahoo Finance (via yfinance)
        2. Stooq (CSV, No API Key Required)
        3. Financial Modeling Prep Stable Quote API (Optional, Needs A Key)

    Fundamentals:
        1. Yahoo Finance (via yfinance) — All Markets
        2. Screener.in (Scraped) — Indian Fundamentals + Quarterly Results
        3. Financial Modeling Prep Stable Ratios-TTM API — Extra US Ratios

    News:
        1. Yahoo Finance Built-In News Feed
        2. Google News RSS
        3. Bing News RSS

    Upcoming Events (Next Earnings / Quarterly Results / Dividend Dates):
        1. Yahoo Finance Calendar Data (via yfinance)
        2. Financial Modeling Prep Stable Earnings API (Optional Fallback)

Other Features:
    - Interactive "Hacker" Loading Animation While A Report Is Being Built,
      Deleted And Replaced With The Final Result Once Ready. Paired With A
      Local "Hacking The Markets" Sticker (Sticker.tgs / Sticker.webp /
      Sticker.webm) Sent Alongside The Animated Text, If One Of Those Files
      Is Present Next To This Script.
    - Personal Portfolio Tracking Backed By A Local SQLite Database
      (Portfolio.db, Created Automatically Next To This Script). Each
      Telegram Chat Gets Its Own Saved List Of Tickers.

Setup:
    pip install python-telegram-bot yfinance pandas requests beautifulsoup4 lxml

Environment Variables (Required Before Running):
    TELEGRAM_BOT_TOKEN   -> Get This From @BotFather On Telegram
    FMP_API_KEY          -> Optional. Free Key From financialmodelingprep.com

    Windows PowerShell:
        $env:TELEGRAM_BOT_TOKEN = "Your-Token-Here"
        $env:FMP_API_KEY = "Your-Key-Here"

    Linux / macOS:
        export TELEGRAM_BOT_TOKEN="Your-Token-Here"
        export FMP_API_KEY="Your-Key-Here"

Run:
    python Bot.py

SECURITY WARNING:
    Never Hardcode A Real Bot Token Or API Key Directly In This File. Both
    Are Now Read From Environment Variables Instead. If A Token Or Key Was
    Ever Committed To This File, Pasted Into A Chat, Or Pushed To A Public
    Repository, Treat It As Compromised And Rotate It Immediately:
        - Telegram Token: Talk To @BotFather -> /revoke
        - FMP Key: Regenerate It From Your FMP Dashboard
"""

import asyncio
import html
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from telegram import Update, BotCommand
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ------------------------------------------------------------------
# Config — Loaded From Environment Variables (Never Hardcode Secrets)
# ------------------------------------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()

FMP_BASE_URL = "https://financialmodelingprep.com/stable"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_last_screener_call = 0.0
SCREENER_MIN_INTERVAL = 2.0  # Seconds Between Requests — Be Polite To Their Servers

REQUEST_TIMEOUT = 10  # Seconds — Shared Timeout For All Outbound HTTP Calls
DIVIDER = "─" * 24

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "Portfolio.db"))

# "Hacking The Markets" Loading Sticker — Place A File Named Sticker.webp In
# The Same Folder As This Script To Have It Sent Alongside The Animated
# Loading Text. If The File Isn't There, The Bot Just Skips It Silently.
# "Hacking The Markets" Loading Sticker — Place A File Named Sticker.tgs
# (Animated), Sticker.webp (Static), Or Sticker.webm (Video) In The Same
# Folder As This Script To Have It Sent Alongside The Animated Loading
# Text. Checked In That Order — Animated First — Since All Three Are Valid
# Formats For Telegram's sendSticker Endpoint. If None Are Present, The Bot
# Just Skips The Sticker Silently.
SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
STICKER_CANDIDATE_PATHS = [
    os.path.join(SCRIPT_DIRECTORY, "Sticker.tgs"),
    os.path.join(SCRIPT_DIRECTORY, "Sticker.webp"),
    os.path.join(SCRIPT_DIRECTORY, "Sticker.webm"),
]

# Known NSE Index Names Mapped To Their Official Constituent CSV File On
# NSE's Public Archives. Send A Message Matching One Of These Names (Spaces
# / Hyphens Are Ignored, E.g. "NIFTY 100" Or "Nifty100" Both Work) To Get A
# Price Snapshot For Every Stock In That Index, Sent One By One.
NSE_INDEX_CONSTITUENT_CSV_URL = "https://archives.nseindia.com/content/indices/{FileName}"
INDEX_CONSTITUENT_CSV_MAP = {
    "NIFTY50": "ind_nifty50list.csv",
    "NIFTYNEXT50": "ind_niftynext50list.csv",
    "NIFTY100": "ind_nifty100list.csv",
    "NIFTY200": "ind_nifty200list.csv",
    "NIFTY500": "ind_nifty500list.csv",
    "NIFTYMIDCAP150": "ind_niftymidcap150list.csv",
    "NIFTYSMALLCAP250": "ind_niftysmallcap250list.csv",
    "NIFTYBANK": "ind_niftybanklist.csv",
    "NIFTYIT": "ind_niftyitlist.csv",
    "NIFTYAUTO": "ind_niftyautolist.csv",
    "NIFTYPHARMA": "ind_niftypharmalist.csv",
    "NIFTYFMCG": "ind_niftyfmcglist.csv",
    "NIFTYMETAL": "ind_niftymetallist.csv",
    "NIFTYENERGY": "ind_niftyenergylist.csv",
    "NIFTYREALTY": "ind_niftyrealtylist.csv",
    "NIFTYFINSERVICE": "ind_niftyfinancelist.csv",
}

# "Hacker" Style Frames Cycled While A Report Is Being Fetched. Deleted And
# Replaced With The Real Result Once Everything Is Ready.
LOADING_FRAMES = [
    "👨‍💻 Hacking The Markets.",
    "👨‍💻 Hacking The Markets..",
    "👨‍💻 Hacking The Markets...",
    "🕵️ Cross-Checking Every Source...",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("StockAssistantBot")
# Quiet Down The Noisy Third-Party HTTP Library Logs So Our Own Title Case
# Log Messages Stay Easy To Read In The Console.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ------------------------------------------------------------------
# Small Shared Helpers
# ------------------------------------------------------------------
def FormatDisplayName(RawInput: str) -> str:
    """Converts Raw User Input Into A Clean Title Case Label For Messages And Logs."""
    return " ".join(RawInput.strip().split()).title()


def SafeGet(Data: dict, *Keys):
    """Returns The First Non-None Value Found Among Several Possible Dict Keys.

    Financial Modeling Prep Occasionally Renames Fields Between API Versions,
    So This Helper Lets Us Check Several Candidate Key Names Without The
    Code Breaking When One Of Them Is Missing.
    """
    for key in Keys:
        value = Data.get(key)
        if value is not None:
            return value
    return None


def NormalizeIndexKey(RawIndexName: str) -> str:
    """Collapses A Free-Text Index Name (E.g. 'Nifty 100', 'nifty-100') Down
    To A Bare Upper-Case Key (E.g. 'NIFTY100') For Dictionary Lookups."""
    return "".join(RawIndexName.strip().upper().replace("-", " ").split())


def ParseTickerList(RawText: str) -> list:
    """Splits Free-Form Text (Comma Or Space Separated) Into A Clean,
    Deduplicated, Upper-Case List Of Ticker Symbols."""
    raw_parts = RawText.replace(",", " ").split()
    seen = set()
    symbols = []
    for part in raw_parts:
        clean_symbol = part.strip().upper()
        if clean_symbol and clean_symbol not in seen:
            seen.add(clean_symbol)
            symbols.append(clean_symbol)
    return symbols


# ------------------------------------------------------------------
# Portfolio Database — SQLite, One Local File, No External Dependencies
# ------------------------------------------------------------------
def InitDatabase():
    """Creates The Portfolio Table In Portfolio.db If It Doesn't Already Exist."""
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS Portfolio (
                ChatId INTEGER NOT NULL,
                Symbol TEXT NOT NULL,
                AddedAt TEXT NOT NULL,
                PRIMARY KEY (ChatId, Symbol)
            )
            """
        )
        connection.commit()
        logger.info("Portfolio Database Ready At '%s'.", DB_PATH)
    finally:
        connection.close()


def AddPortfolioSymbols(ChatId: int, Symbols: list) -> list:
    """Inserts New Tickers Into The Portfolio Table For A Given Chat,
    Silently Skipping Ones Already Saved. Returns The List Actually Added."""
    added = []
    connection = sqlite3.connect(DB_PATH)
    try:
        for symbol in Symbols:
            try:
                connection.execute(
                    "INSERT INTO Portfolio (ChatId, Symbol, AddedAt) VALUES (?, ?, ?)",
                    (ChatId, symbol, datetime.utcnow().isoformat()),
                )
                added.append(symbol)
            except sqlite3.IntegrityError:
                continue  # Already Saved For This Chat
        connection.commit()
    finally:
        connection.close()
    return added


def GetPortfolioSymbols(ChatId: int) -> list:
    """Returns All Tickers Saved For A Given Chat, Alphabetically Sorted."""
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute(
            "SELECT Symbol FROM Portfolio WHERE ChatId = ? ORDER BY Symbol", (ChatId,)
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()


def RemovePortfolioSymbol(ChatId: int, Symbol: str) -> bool:
    """Deletes A Single Ticker From A Chat's Portfolio. Returns True If Something Was Removed."""
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute(
            "DELETE FROM Portfolio WHERE ChatId = ? AND Symbol = ?", (ChatId, Symbol.strip().upper())
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def ClearPortfolio(ChatId: int):
    """Wipes Every Saved Ticker For A Given Chat."""
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("DELETE FROM Portfolio WHERE ChatId = ?", (ChatId,))
        connection.commit()
    finally:
        connection.close()


# ------------------------------------------------------------------
# Yahoo Search — Resolves Company Names ("Kalyan Jewellers") To Tickers
# ------------------------------------------------------------------
def YahooSearchSymbol(Query: str) -> list:
    """
    Looks Up A Free-Text Company Name Via Yahoo Finance's Search Endpoint
    And Returns A List Of {symbol, name, exch} Candidates, Best Match First.
    """
    try:
        url = "https://query2.finance.yahoo.com/v1/finance/search"
        params = {"q": Query, "quotesCount": 6, "newsCount": 0}
        response = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        results = []
        for quote in data.get("quotes", []):
            symbol = quote.get("symbol")
            name = quote.get("shortname") or quote.get("longname") or symbol
            exch = quote.get("exchange", "")
            if symbol:
                results.append({"symbol": symbol, "name": name, "exch": exch})
        return results
    except requests.exceptions.RequestException as error:
        logger.warning("Yahoo Search Request Failed For '%s': %s", Query, error)
        return []
    except Exception as error:
        logger.warning("Yahoo Search Parsing Failed For '%s': %s", Query, error)
        return []


# ------------------------------------------------------------------
# Financial Modeling Prep Search — Second Name Resolver (Fallback)
# ------------------------------------------------------------------
def FmpSearchSymbol(Query: str) -> list:
    """
    Uses The Financial Modeling Prep Stable Search-Name Endpoint As A Second
    Company-Name Resolver. Only Runs If FMP_API_KEY Is Set. Used When Yahoo
    Finance's Own Search Comes Back Empty.
    """
    if not FMP_API_KEY:
        return []
    try:
        url = f"{FMP_BASE_URL}/search-name"
        params = {"query": Query, "apikey": FMP_API_KEY}
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data[:6]:
            symbol = item.get("symbol")
            name = item.get("name") or symbol
            exch = item.get("exchangeFullName") or item.get("exchange", "")
            if symbol:
                results.append({"symbol": symbol, "name": name, "exch": exch})
        return results
    except requests.exceptions.RequestException as error:
        logger.warning("FMP Search Request Failed For '%s': %s", Query, error)
        return []
    except Exception as error:
        logger.warning("FMP Search Parsing Failed For '%s': %s", Query, error)
        return []


# ------------------------------------------------------------------
# Financial Modeling Prep — Stable Quote (Extra Price Fallback)
# ------------------------------------------------------------------
def FetchFmpQuote(Symbol: str) -> dict:
    """Fetches A Real-Time Quote From The FMP Stable API As A Last-Resort Price Source."""
    if not FMP_API_KEY:
        return {}
    try:
        url = f"{FMP_BASE_URL}/quote"
        params = {"symbol": Symbol, "apikey": FMP_API_KEY}
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            return data[0]
        return {}
    except requests.exceptions.RequestException as error:
        logger.warning("FMP Quote Request Failed For '%s': %s", Symbol, error)
        return {}
    except Exception as error:
        logger.warning("FMP Quote Parsing Failed For '%s': %s", Symbol, error)
        return {}


# ------------------------------------------------------------------
# Financial Modeling Prep — Stable TTM Ratios (Extra Fundamentals Fallback)
# ------------------------------------------------------------------
def FetchFmpRatiosTtm(Symbol: str) -> dict:
    """
    Fetches Trailing-Twelve-Month Financial Ratios From The FMP Stable API.
    Uses The Current /stable/ratios-ttm Endpoint (The Old /api/v3/ratios-ttm
    Endpoint Is Deprecated And Was Silently Failing In Earlier Versions Of
    This Bot).
    """
    if not FMP_API_KEY:
        return {}
    try:
        url = f"{FMP_BASE_URL}/ratios-ttm"
        params = {"symbol": Symbol, "apikey": FMP_API_KEY}
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}
    except requests.exceptions.RequestException as error:
        logger.warning("FMP Ratios Request Failed For '%s': %s", Symbol, error)
        return {}
    except Exception as error:
        logger.warning("FMP Ratios Parsing Failed For '%s': %s", Symbol, error)
        return {}


def FetchFmpNextEarnings(Symbol: str) -> dict:
    """
    Fetches The Nearest Upcoming Earnings Entry From The FMP Stable Earnings
    API. Used As A Fallback When Yahoo Finance's Own Calendar Data Doesn't
    Have A Confirmed Or Estimated Earnings Date.
    """
    if not FMP_API_KEY:
        return {}
    try:
        url = f"{FMP_BASE_URL}/earnings"
        params = {"symbol": Symbol, "apikey": FMP_API_KEY}
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        today = datetime.now().date()
        upcoming_entries = []
        for entry in data:
            date_text = entry.get("date")
            if not date_text:
                continue
            try:
                entry_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except ValueError:
                continue
            if entry_date >= today:
                upcoming_entries.append((entry_date, entry))
        if not upcoming_entries:
            return {}
        upcoming_entries.sort(key=lambda pair: pair[0])
        return upcoming_entries[0][1]
    except requests.exceptions.RequestException as error:
        logger.warning("FMP Earnings Request Failed For '%s': %s", Symbol, error)
        return {}
    except Exception as error:
        logger.warning("FMP Earnings Parsing Failed For '%s': %s", Symbol, error)
        return {}


def GetFmpFundamentalsBlock(Symbol: str) -> str:
    """
    Extra US Fundamentals Source — Only Runs If FMP_API_KEY Is Set At The
    Top Of This File. Free Tier Key Available At financialmodelingprep.com
    Field Names Are Checked Against Multiple Candidates Since FMP Has
    Renamed Several Ratio Fields Between API Versions.
    """
    ratios = FetchFmpRatiosTtm(Symbol)
    if not ratios:
        return ""

    lines = ["\n📎 <b>Financial Modeling Prep — Extra Ratios (TTM)</b>"]
    fields = {
        "P/E (TTM)": SafeGet(ratios, "priceToEarningsRatioTTM", "peRatioTTM"),
        "P/B (TTM)": SafeGet(ratios, "priceToBookRatioTTM", "pbRatioTTM"),
        "PEG (TTM)": SafeGet(ratios, "priceToEarningsGrowthRatioTTM", "pegRatioTTM"),
        "Current Ratio (TTM)": SafeGet(ratios, "currentRatioTTM"),
        "Quick Ratio (TTM)": SafeGet(ratios, "quickRatioTTM"),
        "Return On Equity (TTM)": SafeGet(ratios, "returnOnEquityTTM"),
        "Dividend Yield (TTM)": SafeGet(ratios, "dividendYieldTTM", "dividendYielPercentageTTM"),
        "Free Cash Flow / Share (TTM)": SafeGet(ratios, "freeCashFlowPerShareTTM"),
    }
    for label, value in fields.items():
        if value is None:
            continue
        try:
            numeric_value = float(value)
            is_percent_field = "Yield" in label or "Equity" in label
            formatted = f"{numeric_value * 100:.2f}%" if is_percent_field and abs(numeric_value) < 1 else f"{numeric_value:.2f}"
        except (TypeError, ValueError):
            formatted = str(value)
        lines.append(f"{label}: <b>{formatted}</b>")

    return "\n".join(lines) if len(lines) > 1 else ""


# ------------------------------------------------------------------
# NSE Index Constituents (E.g. "NIFTY 100") — Public NSE Archive CSVs
# ------------------------------------------------------------------
def FetchIndexConstituents(RawIndexName: str) -> list:
    """
    Downloads The Official Constituent List For A Known NSE Index (E.g.
    "NIFTY 100", "Nifty Bank") From NSE's Public Archives And Returns The
    Plain Ticker Symbols (No .NS Suffix, Deduplicated, In File Order).
    Returns An Empty List If The Index Name Isn't Recognised Or The
    Download/Parse Fails For Any Reason.
    """
    csv_file_name = INDEX_CONSTITUENT_CSV_MAP.get(NormalizeIndexKey(RawIndexName))
    if not csv_file_name:
        return []
    try:
        url = NSE_INDEX_CONSTITUENT_CSV_URL.format(FileName=csv_file_name)
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text))
        if "Symbol" not in df.columns:
            logger.warning("Index Constituent CSV For '%s' Had No 'Symbol' Column.", RawIndexName)
            return []
        seen = set()
        symbols = []
        for raw_symbol in df["Symbol"].dropna().tolist():
            clean_symbol = str(raw_symbol).strip().upper()
            if clean_symbol and clean_symbol not in seen:
                seen.add(clean_symbol)
                symbols.append(clean_symbol)
        return symbols
    except requests.exceptions.RequestException as error:
        logger.warning("Index Constituent Download Failed For '%s': %s", RawIndexName, error)
        return []
    except Exception as error:
        logger.warning("Index Constituent Parsing Failed For '%s': %s", RawIndexName, error)
        return []


# ------------------------------------------------------------------
# Stooq Fallback (Free, No API Key — Used When Yahoo Finance Fails)
# ------------------------------------------------------------------
def GetStooqHistory(Symbol: str) -> pd.DataFrame:
    """
    Fetches Daily OHLC History From Stooq As A Fallback When Yahoo Finance
    Returns No Data. Stooq Uses A '.us' Suffix For US Tickers. Returns An
    Empty DataFrame If The Symbol Isn't Found There Either.
    """
    candidates = [f"{Symbol.lower()}.us", Symbol.lower()]
    for candidate in candidates:
        try:
            url = f"https://stooq.com/q/d/l/?s={candidate}&i=d"
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code != 200 or "Date" not in response.text:
                continue
            df = pd.read_csv(StringIO(response.text))
            if df.empty or "Close" not in df.columns:
                continue
            df["Date"] = pd.to_datetime(df["Date"])
            df.set_index("Date", inplace=True)
            return df
        except requests.exceptions.RequestException as error:
            logger.warning("Stooq Request Failed For '%s': %s", candidate, error)
            continue
        except Exception as error:
            logger.warning("Stooq Parsing Failed For '%s': %s", candidate, error)
            continue
    return pd.DataFrame()


# ------------------------------------------------------------------
# Screener.in (Indian Stocks — Fundamentals & Quarterly Results)
# ------------------------------------------------------------------
def ScreenerGet(Url: str):
    """Rate-Limited GET So We Don't Hammer Screener's Servers."""
    global _last_screener_call
    elapsed = time.time() - _last_screener_call
    if elapsed < SCREENER_MIN_INTERVAL:
        time.sleep(SCREENER_MIN_INTERVAL - elapsed)
    response = requests.get(Url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
    _last_screener_call = time.time()
    response.raise_for_status()
    return response


def FetchScreenerData(NseSymbol: str) -> dict:
    """
    Scrapes Screener.in For An Indian Company's Ratio Snapshot And Latest
    Quarterly Results Table. NseSymbol Should Be The Plain Code, E.g. "TCS"
    (No .NS/.BO Suffix). Best-Effort Only — Screener's HTML Structure May
    Change And Break Selectors Here.
    """
    result = {"ratios": {}, "quarterly": {}}
    try:
        url = f"https://www.screener.in/company/{NseSymbol}/"
        response = ScreenerGet(url)
        soup = BeautifulSoup(response.text, "lxml")

        ratio_list = soup.select_one("#top-ratios")
        if ratio_list:
            for item in ratio_list.select("li"):
                name_el = item.select_one(".name")
                value_el = item.select_one(".value")
                if name_el and value_el:
                    name = name_el.get_text(strip=True)
                    value = " ".join(value_el.get_text(strip=True).split())
                    result["ratios"][name] = value

        quarters_section = soup.select_one("#quarters")
        if quarters_section:
            table = quarters_section.select_one("table")
            if table:
                headers = [th.get_text(strip=True) for th in table.select("thead th")]
                rows = table.select("tbody tr")
                parsed_rows = []
                for row in rows:
                    cells = [td.get_text(strip=True) for td in row.select("td")]
                    if cells:
                        parsed_rows.append({"label": cells[0], "values": cells[1:]})
                result["quarterly"] = {"headers": headers[1:], "rows": parsed_rows}

        if not result["ratios"] and not result["quarterly"].get("rows"):
            result["error"] = "Page Loaded But No Data Parsed (Layout May Have Changed)."

    except requests.exceptions.HTTPError as error:
        result["error"] = f"Screener Page Not Found Or Blocked ({error})"
    except requests.exceptions.RequestException as error:
        result["error"] = f"Screener Request Failed ({error})"
    except Exception as error:
        result["error"] = f"Screener Fetch Failed ({error})"

    return result


def GetScreenerBlock(NseSymbol: str) -> str:
    lines = ["🇮🇳 <b>Screener.in — Fundamentals</b>"]
    data = FetchScreenerData(NseSymbol)

    if data.get("error"):
        lines.append(f"<i>{data['error']}</i>")
        return "\n".join(lines)

    ratios = data.get("ratios", {})
    if ratios:
        wanted_order = ["Market Cap", "Current Price", "High / Low", "Stock P/E",
                         "Book Value", "Dividend Yield", "ROCE", "ROE", "Face Value"]
        for key in wanted_order:
            if key in ratios:
                lines.append(f"{key}: <b>{ratios[key]}</b>")
        for key, value in ratios.items():
            if key not in wanted_order:
                lines.append(f"{key}: <b>{value}</b>")
    else:
        lines.append("No Ratio Data Found.")

    quarterly = data.get("quarterly", {})
    if quarterly.get("rows"):
        lines.append("\n<b>Quarterly Results (Screener)</b>")
        headers = quarterly.get("headers", [])
        if headers:
            lines.append("Periods: " + " | ".join(headers[-4:]))
        for row in quarterly["rows"]:
            if row["label"] in ("Sales", "Net Profit", "Operating Profit", "EPS in Rs"):
                last_values = row["values"][-4:] if row["values"] else []
                lines.append(f"{row['label']}: " + " | ".join(last_values))

    return "\n".join(lines)


# ------------------------------------------------------------------
# News — Three-Tier Fallback Chain For Reliability
# ------------------------------------------------------------------
def GetNewsItemsFromYfinance(Ticker: yf.Ticker) -> list:
    """Tries Yahoo Finance's Built-In News Feed First (Richest Data When Available)."""
    items = []
    try:
        raw_news = Ticker.news or []
        for entry in raw_news[:6]:
            # yfinance Wraps Fields Under "content" In Newer Versions
            content = entry.get("content", entry)
            title = content.get("title") or entry.get("title")
            summary = content.get("summary") or content.get("description") or ""
            provider = content.get("provider")
            publisher = provider.get("displayName") if isinstance(provider, dict) else entry.get("publisher")
            canonical = content.get("canonicalUrl")
            link = canonical.get("url") if isinstance(canonical, dict) else entry.get("link")
            pub_date = content.get("pubDate") or content.get("displayTime") or ""
            if title and link:
                items.append({
                    "title": title,
                    "publisher": publisher or "Yahoo Finance",
                    "link": link,
                    "summary": " ".join(summary.split())[:160],
                    "published": pub_date[:10] if pub_date else "",
                })
    except Exception as error:
        logger.warning("Yahoo Finance News Lookup Failed: %s", error)
    return items


def GetNewsItemsFromRss(FeedUrl: str, SourceLabel: str, Params: dict) -> list:
    """Shared Parser For RSS-Based News Fallbacks (Google News, Bing News)."""
    items = []
    try:
        response = requests.get(FeedUrl, params=Params, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "xml")
        for item in soup.find_all("item")[:6]:
            title = item.title.get_text(strip=True) if item.title else None
            link = item.link.get_text(strip=True) if item.link else None
            source_tag = item.find("source")
            source = source_tag.get_text(strip=True) if source_tag else SourceLabel
            description_tag = item.find("description")
            summary = description_tag.get_text(strip=True) if description_tag else ""
            summary = BeautifulSoup(summary, "html.parser").get_text()  # Strip Any Embedded HTML
            pub_date_tag = item.find("pubDate")
            published = pub_date_tag.get_text(strip=True)[:16] if pub_date_tag else ""
            if title and link:
                items.append({
                    "title": title,
                    "publisher": source,
                    "link": link,
                    "summary": " ".join(summary.split())[:160],
                    "published": published,
                })
    except requests.exceptions.RequestException as error:
        logger.warning("%s Request Failed: %s", SourceLabel, error)
    except Exception as error:
        logger.warning("%s Parsing Failed: %s", SourceLabel, error)
    return items


def GetNewsItemsFromGoogle(Query: str) -> list:
    """Fallback News Source — Google News RSS, No API Key Required."""
    return GetNewsItemsFromRss(
        "https://news.google.com/rss/search",
        "Google News",
        {"q": f"{Query} stock", "hl": "en-US", "gl": "US", "ceid": "US:en"},
    )


def GetNewsItemsFromBing(Query: str) -> list:
    """Third-Tier Fallback News Source — Bing News RSS, No API Key Required."""
    return GetNewsItemsFromRss(
        "https://www.bing.com/news/search",
        "Bing News",
        {"q": f"{Query} stock", "format": "RSS"},
    )


def GetNewsBlock(Ticker: yf.Ticker, CompanyQuery: str) -> str:
    """
    Builds A Descriptive News Block With Headline, Publisher, Publish Date,
    And A Short Summary Snippet Where Available. Falls Through Yahoo
    Finance -> Google News -> Bing News Until One Source Returns Results.
    """
    lines = ["📰 <b>Latest News</b>"]

    items = GetNewsItemsFromYfinance(Ticker)
    source_label = "Yahoo Finance"

    if not items:
        items = GetNewsItemsFromGoogle(CompanyQuery)
        source_label = "Google News"

    if not items:
        items = GetNewsItemsFromBing(CompanyQuery)
        source_label = "Bing News"

    if not items:
        lines.append("No Recent News Found Across Any Source.")
        return "\n".join(lines)

    lines[0] = f"📰 <b>Latest News</b> <i>(Source: {source_label})</i>"
    for item in items:
        title = html.escape(item["title"])
        meta_bits = [item["publisher"]]
        if item.get("published"):
            meta_bits.append(item["published"])
        meta = " • ".join(meta_bits)
        lines.append(f"• <a href=\"{item['link']}\">{title}</a>\n  <i>{meta}</i>")
        if item.get("summary"):
            lines.append(f"  {html.escape(item['summary'])}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Technical Indicators (Computed Manually, No Extra Dependencies)
# ------------------------------------------------------------------
def ComputeRsi(Close: pd.Series, Length: int = 14) -> pd.Series:
    delta = Close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / Length, min_periods=Length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / Length, min_periods=Length, adjust=False).mean()
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def ComputeMacd(Close: pd.Series, Fast: int = 12, Slow: int = 26, Signal: int = 9):
    ema_fast = Close.ewm(span=Fast, adjust=False).mean()
    ema_slow = Close.ewm(span=Slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=Signal, adjust=False).mean()
    return macd_line, signal_line


def ComputeBollingerBands(Close: pd.Series, Length: int = 20, NumStd: float = 2.0):
    middle_band = Close.rolling(window=Length).mean()
    std_dev = Close.rolling(window=Length).std()
    upper_band = middle_band + (std_dev * NumStd)
    lower_band = middle_band - (std_dev * NumStd)
    return upper_band, middle_band, lower_band


# ------------------------------------------------------------------
# Ticker Resolution
# ------------------------------------------------------------------
def TryDirectHistory(Symbol: str):
    try:
        ticker = yf.Ticker(Symbol)
        history = ticker.history(period="5d")
        if not history.empty:
            return ticker
    except Exception as error:
        logger.debug("Direct History Lookup Failed For '%s': %s", Symbol, error)
    return None


def ResolveTicker(RawInput: str):
    """
    Resolution Order:
        1. Try The Raw Input Directly As A Ticker (Assume US)
        2. Try It As NSE (.NS) Then BSE (.BO)
        3. If All Fail, Treat It As A Company Name And Search Yahoo Finance
           For The Real Ticker (Fixes Inputs Like "Kalyan Jewellers")
        4. If Yahoo Search Has Nothing, Try The Financial Modeling Prep
           Search-Name API As A Second Name Resolver
        5. If Yahoo Has No Price Data At All, Fall Back To Stooq History

    Returns (YfTicker, ResolvedSymbol, StooqHistory).
    """
    raw = RawInput.strip().upper()
    direct_candidates = [raw, f"{raw}.NS", f"{raw}.BO"]

    for symbol in direct_candidates:
        ticker = TryDirectHistory(symbol)
        if ticker is not None:
            return ticker, symbol, pd.DataFrame()

    # Not A Direct Ticker Match — Try Resolving It As A Company Name Via Yahoo
    search_results = YahooSearchSymbol(RawInput)
    for candidate in search_results:
        symbol = candidate["symbol"]
        ticker = TryDirectHistory(symbol)
        if ticker is not None:
            return ticker, symbol, pd.DataFrame()

    # Yahoo Search Found Nothing Useful — Try Financial Modeling Prep's Resolver
    fmp_results = FmpSearchSymbol(RawInput)
    for candidate in fmp_results:
        symbol = candidate["symbol"]
        ticker = TryDirectHistory(symbol)
        if ticker is not None:
            return ticker, symbol, pd.DataFrame()

    # Last Resort — Stooq (Mainly Helps For US Tickers Yahoo Is Rate-Limiting)
    stooq_history = GetStooqHistory(raw)
    if not stooq_history.empty:
        return yf.Ticker(raw), raw, stooq_history

    return None, None, pd.DataFrame()


# ------------------------------------------------------------------
# Report Sections
# ------------------------------------------------------------------
def GetPriceBlock(Ticker: yf.Ticker, Symbol: str, StooqHistory: pd.DataFrame) -> str:
    lines = ["💰 <b>Price</b>"]
    try:
        info = Ticker.info
    except Exception as error:
        logger.warning("Yahoo Finance Info Lookup Failed For '%s': %s", Symbol, error)
        info = {}

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    previous_close = info.get("previousClose")
    currency = info.get("currency", "")
    day_high = info.get("dayHigh")
    day_low = info.get("dayLow")
    week_high = info.get("fiftyTwoWeekHigh")
    week_low = info.get("fiftyTwoWeekLow")
    source_note = ""

    if not price and not StooqHistory.empty:
        source_note = " <i>(Source: Stooq Fallback)</i>"
        price = StooqHistory["Close"].iloc[-1]
        previous_close = StooqHistory["Close"].iloc[-2] if len(StooqHistory) > 1 else None
        day_high = StooqHistory["High"].iloc[-1]
        day_low = StooqHistory["Low"].iloc[-1]

    # Final Fallback — Financial Modeling Prep Stable Quote Endpoint
    if not price and FMP_API_KEY:
        fmp_quote = FetchFmpQuote(Symbol)
        if fmp_quote:
            source_note = " <i>(Source: Financial Modeling Prep Fallback)</i>"
            price = SafeGet(fmp_quote, "price")
            previous_close = SafeGet(fmp_quote, "previousClose")
            currency = currency or "USD"
            day_high = SafeGet(fmp_quote, "dayHigh")
            day_low = SafeGet(fmp_quote, "dayLow")
            week_high = SafeGet(fmp_quote, "yearHigh")
            week_low = SafeGet(fmp_quote, "yearLow")

    if source_note:
        lines[0] = f"💰 <b>Price</b>{source_note}"

    change, change_pct = None, None
    if price and previous_close:
        change = price - previous_close
        change_pct = (change / previous_close) * 100

    if price:
        arrow = "🟢" if (change or 0) >= 0 else "🔴"
        lines.append(f"Current: <b>{price:.2f} {currency}</b> {arrow}")
    if change is not None:
        lines.append(f"Change: {change:+.2f} ({change_pct:+.2f}%)")
    if day_low and day_high:
        lines.append(f"Day Range: {day_low:.2f} – {day_high:.2f}")
    if week_low and week_high:
        lines.append(f"52-Week Range: {week_low:.2f} – {week_high:.2f}")
    if len(lines) == 1:
        lines.append("Price Data Unavailable From All Sources (Yahoo, Stooq, FMP).")
    return "\n".join(lines)


def GetFundamentalsBlock(Ticker: yf.Ticker, Symbol: str) -> str:
    try:
        info = Ticker.info
    except Exception as error:
        logger.warning("Yahoo Finance Fundamentals Lookup Failed For '%s': %s", Symbol, error)
        info = {}

    fields = {
        "Market Cap": info.get("marketCap"),
        "P/E (TTM)": info.get("trailingPE"),
        "Forward P/E": info.get("forwardPE"),
        "EPS (TTM)": info.get("trailingEps"),
        "P/B": info.get("priceToBook"),
        "ROE": info.get("returnOnEquity"),
        "Debt/Equity": info.get("debtToEquity"),
        "Profit Margin": info.get("profitMargins"),
        "Sector": info.get("sector"),
        "Industry": info.get("industry"),
    }

    def FormatValue(Key, Value):
        if Value is None:
            return None
        if Key == "Market Cap":
            return f"{Value:,.0f}"
        if Key in ("ROE", "Profit Margin") and isinstance(Value, float):
            return f"{Value * 100:.2f}%"
        if isinstance(Value, float):
            return f"{Value:.2f}"
        return str(Value)

    lines = ["📊 <b>Fundamentals</b>"]
    for key, value in fields.items():
        formatted = FormatValue(key, value)
        if formatted:
            lines.append(f"{key}: <b>{formatted}</b>")
    if len(lines) == 1:
        lines.append("Fundamentals Unavailable For This Ticker From Yahoo Finance.")
    return "\n".join(lines)


def GetQuarterlyBlock(Ticker: yf.Ticker) -> str:
    lines = ["🗓️ <b>Quarterly Results (Latest)</b>"]
    try:
        # Newer yfinance Versions Prefer quarterly_income_stmt; Older Ones
        # Only Have quarterly_financials. Try Both For Compatibility.
        quarterly_income = getattr(Ticker, "quarterly_income_stmt", None)
        if quarterly_income is None or quarterly_income.empty:
            quarterly_income = Ticker.quarterly_financials

        if quarterly_income is not None and not quarterly_income.empty:
            latest_col = quarterly_income.columns[0]
            date_str = latest_col.strftime("%b %Y") if hasattr(latest_col, "strftime") else str(latest_col)
            lines.append(f"Period Ending: <b>{date_str}</b>")
            for row_name in ["Total Revenue", "Gross Profit", "Net Income", "Operating Income"]:
                if row_name in quarterly_income.index:
                    value = quarterly_income.loc[row_name, latest_col]
                    if pd.notna(value):
                        lines.append(f"{row_name}: {value:,.0f}")
        else:
            lines.append("Quarterly Data Not Available For This Ticker.")
    except Exception as error:
        logger.warning("Quarterly Results Lookup Failed: %s", error)
        lines.append(f"Quarterly Data Unavailable ({error})")
    return "\n".join(lines)


def GetDividendsBlock(Ticker: yf.Ticker) -> str:
    lines = ["💵 <b>Dividends</b>"]
    try:
        dividends = Ticker.dividends
        if dividends is not None and not dividends.empty:
            for date, amount in dividends.tail(5).items():
                lines.append(f"{date.strftime('%d %b %Y')}: {amount:.2f}")
            yield_pct = Ticker.info.get("dividendYield")
            if yield_pct:
                lines.append(f"Dividend Yield: <b>{yield_pct * 100:.2f}%</b>")
        else:
            lines.append("No Dividend History Found.")
    except Exception as error:
        logger.warning("Dividend Lookup Failed: %s", error)
        lines.append(f"Dividend Data Unavailable ({error})")
    return "\n".join(lines)


def GetUpcomingEventsBlock(Ticker: yf.Ticker, Symbol: str) -> str:
    """
    Builds A Block Showing The Next Expected Quarterly Earnings Date, The
    Next Quarterly Results Date, And The Next Dividend / Ex-Dividend Date.
    Tries Yahoo Finance's Calendar Data First, Then Falls Back To Financial
    Modeling Prep's Earnings API For The Earnings/Results Date If Yahoo Has
    Nothing.

    Note: For Almost Every Listed Company, "Earnings Date" And "Quarterly
    Results Date" Refer To The Same Event (The Day The Company Reports Its
    Quarterly Numbers), So Both Lines Are Sourced From The Same Resolved
    Date Rather Than Two Separate Lookups.
    """
    lines = ["📅 <b>Upcoming Events</b>"]
    earnings_date_text = None
    earnings_source_note = ""
    ex_dividend_text = None
    dividend_pay_text = None

    try:
        calendar = Ticker.get_calendar() if hasattr(Ticker, "get_calendar") else Ticker.calendar
        if isinstance(calendar, dict):
            earnings_dates = calendar.get("Earnings Date")
            if earnings_dates:
                if isinstance(earnings_dates, (list, tuple)):
                    earnings_date_text = " To ".join(str(single_date) for single_date in earnings_dates)
                else:
                    earnings_date_text = str(earnings_dates)
            ex_dividend_raw = calendar.get("Ex-Dividend Date")
            if ex_dividend_raw:
                ex_dividend_text = str(ex_dividend_raw)
            dividend_pay_raw = calendar.get("Dividend Date")
            if dividend_pay_raw:
                dividend_pay_text = str(dividend_pay_raw)
        elif hasattr(calendar, "empty") and not calendar.empty and "Earnings Date" in calendar.index:
            # Some Older yfinance Versions Return A DataFrame Instead Of A Dict
            earnings_date_text = str(calendar.loc["Earnings Date"].iloc[0])
    except Exception as error:
        logger.warning("Yahoo Finance Calendar Lookup Failed For '%s': %s", Symbol, error)

    if not ex_dividend_text or not dividend_pay_text:
        try:
            info = Ticker.info
            ex_dividend_ts = info.get("exDividendDate")
            if ex_dividend_ts and not ex_dividend_text:
                ex_dividend_text = datetime.fromtimestamp(ex_dividend_ts).strftime("%d %b %Y")
            dividend_pay_ts = info.get("dividendDate")
            if dividend_pay_ts and not dividend_pay_text:
                dividend_pay_text = datetime.fromtimestamp(dividend_pay_ts).strftime("%d %b %Y")
        except Exception as error:
            logger.warning("Yahoo Finance Dividend Date Lookup Failed For '%s': %s", Symbol, error)

    if not earnings_date_text and FMP_API_KEY:
        fmp_earnings = FetchFmpNextEarnings(Symbol)
        if fmp_earnings:
            date_text = fmp_earnings.get("date", "Unknown Date")
            timing = fmp_earnings.get("time", "")
            eps_estimate = fmp_earnings.get("epsEstimated")
            timing_note = f" ({timing.upper()})" if timing else ""
            earnings_date_text = f"{date_text}{timing_note}"
            if eps_estimate is not None:
                earnings_date_text += f" — Estimated EPS: {eps_estimate}"
            earnings_source_note = " <i>(Source: Financial Modeling Prep)</i>"

    lines.append(f"Next Earnings Date: <b>{earnings_date_text or 'Not Announced Yet'}</b>{earnings_source_note}")
    # Same Underlying Event As "Next Earnings Date" Above — Shown Separately
    # Under The "Quarterly Results" Label Since That's The Term Most Indian
    # (NSE/BSE) Users And Screener.in Use For This Announcement.
    lines.append(f"Next Quarterly Results Date: <b>{earnings_date_text or 'Not Announced Yet'}</b>{earnings_source_note}")
    if ex_dividend_text:
        lines.append(f"Next Ex-Dividend Date: <b>{ex_dividend_text}</b>")
    if dividend_pay_text:
        lines.append(f"Next Dividend Payment Date: <b>{dividend_pay_text}</b>")
    if not ex_dividend_text and not dividend_pay_text:
        lines.append("No Upcoming Dividend Date Announced.")

    return "\n".join(lines)


def GetTechnicalsBlock(Ticker: yf.Ticker, StooqHistory: pd.DataFrame) -> str:
    lines = ["📈 <b>Technical Analysis</b>"]
    try:
        history = Ticker.history(period="6mo")
        source_note = ""
        if history.empty and not StooqHistory.empty:
            history = StooqHistory
            source_note = " <i>(Source: Stooq Fallback)</i>"

        if history.empty or len(history) < 20:
            lines.append("Not Enough Historical Data For Indicators.")
            return "\n".join(lines)

        lines[0] = f"📈 <b>Technical Analysis</b>{source_note}"
        close = history["Close"]

        rsi_series = ComputeRsi(close, 14)
        rsi = rsi_series.iloc[-1]
        if pd.notna(rsi):
            signal_label = "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral"
            lines.append(f"RSI (14): <b>{rsi:.1f}</b> ({signal_label})")

        macd_line, signal_line = ComputeMacd(close)
        macd_value = macd_line.iloc[-1]
        signal_value = signal_line.iloc[-1]
        if pd.notna(macd_value) and pd.notna(signal_value):
            trend = "Bullish" if macd_value > signal_value else "Bearish"
            lines.append(f"MACD: {macd_value:.2f} / Signal: {signal_value:.2f} ({trend})")

        sma20 = close.rolling(window=20).mean().iloc[-1]
        sma50 = close.rolling(window=50).mean().iloc[-1] if len(close) >= 50 else None
        last_close = close.iloc[-1]

        if pd.notna(sma20):
            if sma50 is not None and pd.notna(sma50):
                trend = "Uptrend" if sma20 > sma50 else "Downtrend"
                lines.append(f"SMA 20: {sma20:.2f} | SMA 50: {sma50:.2f} ({trend})")
            else:
                lines.append(f"SMA 20: {sma20:.2f}")
            lines.append(f"Price Vs SMA 20: {'Above' if last_close > sma20 else 'Below'}")

        upper_band, middle_band, lower_band = ComputeBollingerBands(close)
        if pd.notna(upper_band.iloc[-1]) and pd.notna(lower_band.iloc[-1]):
            position = "Near Upper Band" if last_close >= middle_band.iloc[-1] else "Near Lower Band"
            lines.append(
                f"Bollinger Bands: {lower_band.iloc[-1]:.2f} – {upper_band.iloc[-1]:.2f} ({position})"
            )

    except Exception as error:
        logger.warning("Technical Analysis Computation Failed: %s", error)
        lines.append(f"Technicals Unavailable ({error})")
    return "\n".join(lines)


def BuildFullReport(SymbolInput: str) -> str:
    display_name = FormatDisplayName(SymbolInput)
    ticker, resolved, stooq_history = ResolveTicker(SymbolInput)

    if ticker is None:
        logger.info("Could Not Resolve Any Data Source For '%s'.", display_name)
        return (
            f"❌ <b>Couldn't Find Data For {display_name}.</b>\n"
            "Try The Exact Ticker Symbol, Or Use /search To Look Up The Right One "
            "(E.g. AAPL For US, RELIANCE Or TCS For India)."
        )

    try:
        company_name = ticker.info.get("longName", resolved)
    except Exception as error:
        logger.warning("Company Name Lookup Failed For '%s': %s", resolved, error)
        company_name = resolved

    header = f"📌 <b>{company_name}</b> (<code>{resolved}</code>)\n{DIVIDER}"

    blocks = [
        GetPriceBlock(ticker, resolved, stooq_history),
        GetFundamentalsBlock(ticker, resolved),
        GetQuarterlyBlock(ticker),
        GetDividendsBlock(ticker),
        GetUpcomingEventsBlock(ticker, resolved),
        GetTechnicalsBlock(ticker, stooq_history),
        GetNewsBlock(ticker, company_name),
    ]

    # Indian Tickers Get Extra Fundamentals + Quarterly Data From Screener.in
    if resolved.endswith(".NS") or resolved.endswith(".BO"):
        plain_symbol = resolved.rsplit(".", 1)[0]
        blocks.append(GetScreenerBlock(plain_symbol))

    # US Tickers Optionally Get Extra Ratios From Financial Modeling Prep
    if not resolved.endswith((".NS", ".BO")) and FMP_API_KEY:
        fmp_block = GetFmpFundamentalsBlock(resolved)
        if fmp_block:
            blocks.append(fmp_block)

    body = f"\n\n{DIVIDER}\n".join(blocks)
    return f"{header}\n\n{body}"


# ------------------------------------------------------------------
# Telegram Handlers
# ------------------------------------------------------------------
async def StartCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    await Update_.message.reply_text(
        "👋 <b>Welcome To Stock Info Bot!</b>\n\n"
        "Just Send Me A Stock Name Or Ticker (E.g. AAPL, TCS, RELIANCE, Kalyan Jewellers) "
        "And I'll Send Back Price, Fundamentals, Quarterly Results, Dividends, Upcoming "
        "Earnings/Quarterly Results/Dividend Dates, News, And Technical Analysis — Pulled "
        "From Multiple Sources With Automatic Fallbacks If One Source Is Down.\n\n"
        "You Can Also Send An NSE Index Name Like <code>NIFTY 100</code> Or "
        "<code>NIFTY BANK</code> To Get A Price Snapshot Of Every Stock In That Index, "
        "One By One.\n\n"
        "<b>Commands</b>\n"
        "/start — Show This Message\n"
        "/help — Detailed Usage Guide\n"
        "/search — Look Up A Ticker By Company Name\n"
        "/price — Quick Price Check\n"
        "/news — Latest News For A Stock\n"
        "/portfolio — View Or Build Your Saved Watchlist\n"
        "/addstock — Add Tickers To Your Portfolio\n"
        "/removestock — Remove One Ticker From Your Portfolio\n"
        "/mystocks — List Your Saved Tickers\n"
        "/clearportfolio — Wipe Your Whole Portfolio",
        parse_mode=ParseMode.HTML,
    )


async def HelpCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    await Update_.message.reply_text(
        "<b>How To Use This Bot</b>\n"
        f"{DIVIDER}\n"
        "Send Any Ticker Or Company Name As A Plain Message To Get The Full Report: "
        "Price, Fundamentals, Quarterly Results, Dividends, Upcoming Earnings/Quarterly "
        "Results/Dividend Dates, Technicals, And News In One Reply.\n\n"
        "<b>Examples You Can Send</b>\n"
        "🇺🇸 US Tickers: <code>AAPL</code>, <code>MSFT</code>, <code>TSLA</code>\n"
        "🇮🇳 Indian Tickers: <code>RELIANCE</code>, <code>TCS</code>, <code>INFY</code>, <code>HDFCBANK</code>\n"
        "🏢 Company Names Also Work: <code>Kalyan Jewellers</code>, <code>Reliance Industries</code>\n\n"
        "<b>NSE Index Lookup</b>\n"
        "Send An Index Name Like <code>NIFTY 100</code>, <code>NIFTY 50</code>, Or "
        "<code>NIFTY BANK</code> And I'll Pull The Official Constituent List From NSE "
        "And Send A Price Snapshot For Every Stock In It, One By One (Pace-Limited So "
        "We Don't Get Rate-Limited By Telegram). Send A Single Ticker Any Time For Its "
        "Full Report.\n\n"
        "<b>Slash Commands</b>\n"
        "/search &lt;name&gt; — Lists Matching Tickers So You Can Pick The Right One\n"
        "/price &lt;name&gt; — Just The Current Price And Day Range\n"
        "/news &lt;name&gt; — Just The Latest Headlines With Summaries\n\n"
        "<b>Portfolio Watchlist</b>\n"
        "/portfolio — Shows A Fresh Price Snapshot For Every Saved Ticker. If You Have "
        "None Saved Yet, It Will Ask You To Send Your Tickers (Comma Or Space Separated).\n"
        "/addstock AAPL, TCS — Adds More Tickers Any Time\n"
        "/mystocks — Lists What's Currently Saved\n"
        "/removestock AAPL — Removes One Ticker\n"
        "/clearportfolio — Removes Everything\n"
        "Your List Is Stored Locally In A SQLite Database And Is Private To This Chat.\n\n"
        "<b>How Ticker Matching Works</b>\n"
        "1️⃣ Tries Your Input As A Direct Ticker (US)\n"
        "2️⃣ Then Tries It As An NSE (.NS) And BSE (.BO) Ticker\n"
        "3️⃣ Then Searches Yahoo Finance By Company Name\n"
        "4️⃣ Then Searches Financial Modeling Prep By Company Name\n"
        "5️⃣ Finally Falls Back To Stooq's Historical Data If Yahoo Has None\n\n"
        "<b>Tip:</b> If A Company Name Doesn't Resolve, Use /search First To Confirm "
        "The Exact Ticker, Then Send That Ticker Directly For The Fastest, Most "
        "Accurate Report.",
        parse_mode=ParseMode.HTML,
    )


async def SearchCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(Context.args) if Context.args else ""
    if not query:
        await Update_.message.reply_text("Usage: <code>/search Company Name</code>", parse_mode=ParseMode.HTML)
        return

    display_query = FormatDisplayName(query)
    results = YahooSearchSymbol(query) or FmpSearchSymbol(query)
    if not results:
        await Update_.message.reply_text(f"No Matches Found For {display_query}.")
        return

    lines = [f"🔍 <b>Search Results For {display_query}</b>"]
    for result in results[:5]:
        lines.append(f"<code>{result['symbol']}</code> — {result['name']} ({result['exch']})")
    await Update_.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def PriceCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(Context.args) if Context.args else ""
    if not query:
        await Update_.message.reply_text("Usage: <code>/price AAPL</code>", parse_mode=ParseMode.HTML)
        return

    display_query = FormatDisplayName(query)
    ticker, resolved, stooq_history = ResolveTicker(query)
    if ticker is None:
        await Update_.message.reply_text(f"❌ Couldn't Find Price Data For {display_query}.")
        return

    block = GetPriceBlock(ticker, resolved, stooq_history)
    await Update_.message.reply_text(f"<code>{resolved}</code>\n{block}", parse_mode=ParseMode.HTML)


async def NewsCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(Context.args) if Context.args else ""
    if not query:
        await Update_.message.reply_text("Usage: <code>/news AAPL</code>", parse_mode=ParseMode.HTML)
        return

    display_query = FormatDisplayName(query)
    ticker, resolved, _ = ResolveTicker(query)
    if ticker is None:
        await Update_.message.reply_text(f"❌ Couldn't Find {display_query} To Fetch News For.")
        return

    block = GetNewsBlock(ticker, query)
    await Update_.message.reply_text(
        f"<code>{resolved}</code>\n{block}", parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def RunLoadingAnimation(Message, StopEvent: asyncio.Event, Frames=None):
    """
    Cycles A Telegram Message Through A Sequence Of "Hacker Working" Frames
    Until StopEvent Is Set. The Caller Is Responsible For Deleting Or
    Replacing This Message Once The Real Result Is Ready.
    """
    frames = Frames or LOADING_FRAMES
    frame_index = 0
    while not StopEvent.is_set():
        try:
            await Message.edit_text(frames[frame_index % len(frames)])
        except Exception:
            pass  # Harmless — E.g. "Message Is Not Modified" Telegram Errors
        frame_index += 1
        try:
            await asyncio.wait_for(StopEvent.wait(), timeout=1.1)
        except asyncio.TimeoutError:
            pass  # Expected — Just Means It's Time For The Next Frame


async def RunTypingIndicator(Bot, ChatId: int, StopEvent: asyncio.Event):
    """
    Keeps Telegram's Native "Bot Is Typing..." Indicator Alive In A Chat
    Until StopEvent Is Set. Telegram Only Shows The Indicator For A Few
    Seconds Per Call, So This Re-Sends It Every 4 Seconds To Keep It
    Visible Through Gaps Between Messages (E.g. While Working Through A
    Long List Of Tickers) Without Posting Any Extra Text Or Sticker.
    """
    while not StopEvent.is_set():
        try:
            await Bot.send_chat_action(chat_id=ChatId, action=ChatAction.TYPING)
        except Exception:
            pass  # Harmless — Chat Action Failures Shouldn't Interrupt The Fetch
        try:
            await asyncio.wait_for(StopEvent.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass  # Expected — Just Means It's Time To Refresh The Indicator


async def SendLoadingSticker(Update_: Update):
    """
    Sends The Local "Hacking The Markets" Sticker So It Shows Up Alongside
    The Animated Loading Text While A Report Is Being Built. Looks For
    Sticker.tgs, Sticker.webp, Or Sticker.webm (In That Order) Next To This
    Script. Returns The Sent Sticker Message (So It Can Be Deleted Once The
    Real Report Is Ready), Or None If No Sticker File Was Found Or Sending
    Failed.
    """
    sticker_path = next((path for path in STICKER_CANDIDATE_PATHS if os.path.isfile(path)), None)
    if sticker_path is None:
        logger.warning(
            "No Loading Sticker Found (Checked %s) — Skipping Sticker.",
            ", ".join(STICKER_CANDIDATE_PATHS),
        )
        return None
    try:
        with open(sticker_path, "rb") as sticker_file:
            return await Update_.message.reply_sticker(sticker_file)
    except Exception as error:
        logger.warning("Failed To Send Loading Sticker: %s", error)
        return None


async def SendIndexConstituents(Update_: Update, Context: ContextTypes.DEFAULT_TYPE, IndexDisplayName: str, Symbols: list):
    """
    Sends A Quick Price Snapshot For Every Ticker In An NSE Index, One
    Message At A Time (E.g. Typing "NIFTY 100" Sends All 100 Constituents
    In Turn), Each New Message Pushing The Previous Ones Up. Deliberately
    Uses No Status Message, Sticker, Or Emoji-Heavy Banner Here — Instead,
    Telegram's Native "Typing..." Indicator Is Kept Alive In The Background
    So There's Always Something At The Bottom Of The Chat Showing Activity
    During The Gaps Between Stocks. Reuses The Same Price Block As
    /portfolio — Send The Individual Ticker Name Any Time For Its Full
    Report (Fundamentals, Quarterly Results, News, Technicals, Etc).
    """
    chat_id = Update_.effective_chat.id
    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(RunTypingIndicator(Context.bot, chat_id, stop_event))

    sent_count = 0
    try:
        for symbol in Symbols:
            ticker, resolved, stooq_history = await asyncio.to_thread(ResolveTicker, symbol)
            if ticker is None:
                await Update_.message.reply_text(f"{symbol} — Couldn't Fetch Data From Any Source.")
            else:
                price_block = await asyncio.to_thread(GetPriceBlock, ticker, resolved, stooq_history)
                await Update_.message.reply_text(f"<code>{resolved}</code>\n{price_block}", parse_mode=ParseMode.HTML)
                sent_count += 1
            await asyncio.sleep(1.1)  # Stay Comfortably Under Telegram's Per-Chat Rate Limit
    finally:
        stop_event.set()
        await typing_task

    await Update_.message.reply_text(
        f"Done — Sent {sent_count}/{len(Symbols)} {IndexDisplayName} Stocks.\n"
        "Send Any Single Ticker Name For Its Full Report."
    )


async def HandleMessage(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    user_input = Update_.message.text.strip()
    if not user_input:
        return

    # If We're Waiting On A Reply To "Send Me Your Tickers", Treat This
    # Message As Portfolio Input Instead Of A Normal Stock Lookup.
    if Context.chat_data.get("AwaitingPortfolioInput"):
        await HandlePortfolioInput(Update_, Context, user_input)
        return

    # If The Message Matches A Known NSE Index Name (E.g. "NIFTY 100",
    # "Nifty Bank"), Fetch Its Official Constituent List And Send A Price
    # Snapshot For Every Stock In It, One By One, Instead Of Treating The
    # Whole Index Name As A Single Ticker Lookup.
    if NormalizeIndexKey(user_input) in INDEX_CONSTITUENT_CSV_MAP:
        index_display_name = FormatDisplayName(user_input)
        logger.info("Received Index Request For '%s' From Chat %s.", index_display_name, Update_.effective_chat.id)
        symbols = await asyncio.to_thread(FetchIndexConstituents, user_input)
        if not symbols:
            await Update_.message.reply_text(
                f"❌ Couldn't Fetch The Constituent List For {index_display_name}. "
                "NSE's Archive May Be Temporarily Unavailable — Try Again Shortly."
            )
            return
        await SendIndexConstituents(Update_, Context, index_display_name, symbols)
        return

    display_name = FormatDisplayName(user_input)
    logger.info("Received Request For '%s' From Chat %s.", display_name, Update_.effective_chat.id)

    sticker_message = await SendLoadingSticker(Update_)
    loading_message = await Update_.message.reply_text(LOADING_FRAMES[0])
    stop_event = asyncio.Event()
    animation_task = asyncio.create_task(RunLoadingAnimation(loading_message, stop_event))

    try:
        # Run The Blocking Network Calls In A Background Thread So The
        # Loading Animation Keeps Cycling Smoothly While We Fetch Data.
        report = await asyncio.to_thread(BuildFullReport, user_input)
    except Exception as error:
        logger.exception("Error Building Report For '%s'.", display_name)
        report = f"⚠️ Something Went Wrong Fetching Data: {error}"
    finally:
        stop_event.set()
        await animation_task

    try:
        await loading_message.delete()
    except Exception as error:
        logger.warning("Couldn't Delete The Loading Message: %s", error)

    if sticker_message:
        try:
            await sticker_message.delete()
        except Exception as error:
            logger.warning("Couldn't Delete The Loading Sticker: %s", error)

    # Telegram Message Length Limit Is 4096 Characters — Split If Needed
    if len(report) <= 4000:
        await Update_.message.reply_text(report, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        chunks = []
        current_chunk = ""
        for line in report.split("\n"):
            if len(current_chunk) + len(line) + 1 > 3900:  # 
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        total_parts = len(chunks)
        for index, chunk in enumerate(chunks, 1):
            if total_parts > 1:
                chunk_text = f"<i>(Part {index} Of {total_parts})</i>\n\n{chunk}"
            else:
                chunk_text = chunk

            await Update_.message.reply_text(
                chunk_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )


# ------------------------------------------------------------------
# Portfolio Commands
# ------------------------------------------------------------------
async def HandlePortfolioInput(Update_: Update, Context: ContextTypes.DEFAULT_TYPE, RawText: str):
    """Parses A Free-Form Reply Of Tickers And Saves Them To The Chat's Portfolio."""
    chat_id = Update_.effective_chat.id
    Context.chat_data["AwaitingPortfolioInput"] = False

    symbols = ParseTickerList(RawText)
    if not symbols:
        await Update_.message.reply_text(
            "I Didn't Catch Any Valid Tickers In That. Send /portfolio Again To Retry."
        )
        return

    added = AddPortfolioSymbols(chat_id, symbols)
    all_symbols = GetPortfolioSymbols(chat_id)

    if added:
        await Update_.message.reply_text(
            f"✅ Added: <b>{', '.join(added)}</b>\n"
            f"📋 Your Portfolio Now Has {len(all_symbols)} Ticker(s): {', '.join(all_symbols)}\n\n"
            "Send /portfolio Any Time For A Fresh Snapshot Of Every Saved Ticker.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await Update_.message.reply_text("Those Tickers Are Already In Your Portfolio.")


async def PortfolioCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    """
    Shows A Fresh Price Snapshot For Every Ticker Saved In This Chat's
    Portfolio, Sending One Message Per Ticker. If Nothing Is Saved Yet,
    Asks The User To Send Their Tickers First.
    """
    chat_id = Update_.effective_chat.id
    symbols = GetPortfolioSymbols(chat_id)

    if not symbols:
        Context.chat_data["AwaitingPortfolioInput"] = True
        await Update_.message.reply_text(
            "📋 You Don't Have Any Tickers Saved Yet.\n\n"
            "Send Me Your Tickers Now, Separated By Commas Or Spaces "
            "(E.g. <code>AAPL, TCS, RELIANCE, MSFT</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    status_message = await Update_.message.reply_text(
        f"👨‍💻 Fetching Your Portfolio ({len(symbols)} Ticker(s))..."
    )

    for symbol in symbols:
        ticker, resolved, stooq_history = await asyncio.to_thread(ResolveTicker, symbol)
        if ticker is None:
            await Update_.message.reply_text(
                f"❌ <b>{symbol}</b> — Couldn't Fetch Data From Any Source.", parse_mode=ParseMode.HTML
            )
            continue
        price_block = await asyncio.to_thread(GetPriceBlock, ticker, resolved, stooq_history)
        await Update_.message.reply_text(f"<code>{resolved}</code>\n{price_block}", parse_mode=ParseMode.HTML)

    try:
        await status_message.delete()
    except Exception:
        pass


async def AddStockCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    raw_args = " ".join(Context.args) if Context.args else ""

    if not raw_args:
        Context.chat_data["AwaitingPortfolioInput"] = True
        await Update_.message.reply_text(
            "Send Me The Tickers To Add, Separated By Commas Or Spaces "
            "(E.g. <code>AAPL, TCS, RELIANCE</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    await HandlePortfolioInput(Update_, Context, raw_args)


async def RemoveStockCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    chat_id = Update_.effective_chat.id
    query = " ".join(Context.args) if Context.args else ""
    if not query:
        await Update_.message.reply_text("Usage: <code>/removestock AAPL</code>", parse_mode=ParseMode.HTML)
        return

    removed = RemovePortfolioSymbol(chat_id, query)
    clean_symbol = query.strip().upper()
    if removed:
        await Update_.message.reply_text(
            f"🗑️ Removed <b>{clean_symbol}</b> From Your Portfolio.", parse_mode=ParseMode.HTML
        )
    else:
        await Update_.message.reply_text(f"{clean_symbol} Wasn't In Your Portfolio.")


async def MyStocksCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    chat_id = Update_.effective_chat.id
    symbols = GetPortfolioSymbols(chat_id)
    if not symbols:
        await Update_.message.reply_text("Your Portfolio Is Empty. Send /portfolio To Add Tickers.")
        return
    await Update_.message.reply_text(
        f"📋 <b>Your Portfolio ({len(symbols)} Ticker(s))</b>\n" + ", ".join(symbols),
        parse_mode=ParseMode.HTML,
    )


async def ClearPortfolioCommand(Update_: Update, Context: ContextTypes.DEFAULT_TYPE):
    chat_id = Update_.effective_chat.id
    ClearPortfolio(chat_id)
    await Update_.message.reply_text("🗑️ Your Portfolio Has Been Cleared.")


async def PostInit(Application_: Application):
    """Registers The Slash-Command Menu Shown In Telegram's UI."""
    await Application_.bot.set_my_commands([
        BotCommand("start", "🚀 Start The Bot"),
        BotCommand("help", "❓ Show Detailed Help"),
        BotCommand("search", "🔍 Search For A Company"),
        BotCommand("price", "📈 Get Stock Price"),
        BotCommand("news", "📰 Get Latest News"),
        BotCommand("portfolio", "📋 View Or Build Your Portfolio"),
        BotCommand("addstock", "➕ Add Tickers To Portfolio"),
        BotCommand("removestock", "➖ Remove A Ticker"),
        BotCommand("mystocks", "📄 List Saved Tickers"),
        BotCommand("clearportfolio", "🧹 Clear Your Portfolio"),
    ])
    logger.info("Bot Command Menu Registered Successfully.")


async def PostShutdown(Application_: Application):
    """Runs Cleanup Logging When The Bot Stops (E.g. On Ctrl+C Or SIGTERM)."""
    logger.info("Polling Stopped. Releasing Resources And Closing Connections...")


def Main():
    if not BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN Environment Variable Is Not Set. "
            "Get A Token From @BotFather And Set It Before Running This Bot."
        )
        raise SystemExit(1)

    if not FMP_API_KEY:
        logger.warning(
            "FMP_API_KEY Is Not Set — Financial Modeling Prep Fallbacks Will Be Skipped. "
            "This Is Optional But Recommended For Extra Reliability."
        )

    InitDatabase()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(PostInit)
        .post_shutdown(PostShutdown)
        .concurrent_updates(8)
        .connection_pool_size(16)
        .build()
    )

    app.add_handler(CommandHandler("start", StartCommand))
    app.add_handler(CommandHandler("help", HelpCommand))
    app.add_handler(CommandHandler("search", SearchCommand))
    app.add_handler(CommandHandler("price", PriceCommand))
    app.add_handler(CommandHandler("news", NewsCommand))
    app.add_handler(CommandHandler("portfolio", PortfolioCommand))
    app.add_handler(CommandHandler("addstock", AddStockCommand))
    app.add_handler(CommandHandler("removestock", RemoveStockCommand))
    app.add_handler(CommandHandler("mystocks", MyStocksCommand))
    app.add_handler(CommandHandler("clearportfolio", ClearPortfolioCommand))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, HandleMessage))

    logger.info("Bot Starting Up And Polling For Messages... Press Ctrl+C To Stop.")

    try:
        # run_polling Already Installs Handlers For SIGINT/SIGTERM/SIGABRT
        # Internally And Exits Its Own Loop Cleanly On Ctrl+C. The Extra
        # try/except Here Guards Against Any Interrupt That Slips Through
        # (For Example On Some Windows Terminal Setups) So We Never Print
        # An Ugly Raw Traceback To The User.
        app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
    except KeyboardInterrupt:
        logger.info("Keyboard Interrupt Received. Shutting Down Gracefully...")
    except Exception as error:
        logger.exception("Bot Crashed With An Unexpected Error: %s", error)
        sys.exit(1)
    finally:
        logger.info("Bot Shutdown Complete. Goodbye!")


if __name__ == "__main__":
    Main()