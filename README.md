# VerifyTrade Bot

A Telegram Stock Assistant Bot That Provides Price, Fundamentals, Quarterly Results, Dividends, News, And Technical Analysis For Both US And Indian (NSE/BSE) Stocks.

## Features

*   **Real-Time Price Data**: Get The Latest Price For Any Supported Ticker.
*   **Fundamental Analysis**: View Key Ratios And Financials.
*   **Quarterly Results**: Stay Updated With The Latest Earnings Reports.
*   **Dividend Information**: Track Upcoming And Past Dividends.
*   **News Integration**: Read The Latest News Affecting Your Stocks.
*   **Portfolio Tracking**: Save Your Favorite Tickers To Your Personal Portfolio Backed By A Local SQLite Database.
*   **Interactive Loading**: Enjoy A Cool "Hacker" Loading Animation While Your Report Is Being Generated.

## Architecture

```text
+--------------------+       +--------------------+
|                    |       |                    |
|   Telegram User    | <---> |  VerifyTrade Bot   |
|                    |       |                    |
+--------------------+       +--------------------+
                                |
       +------------------------+------------------------+
       |                        |                        |
       v                        v                        v
+--------------------+   +--------------------+   +--------------------+
|                    |   |                    |   |                    |
|  Local Database    |   |    Data APIs       |   |    News Sources    |
|   (SQLite 3)       |   | (Yahoo Finance,    |   | (Google, Bing,     |
|                    |   |  FMP, Stooq,       |   |  Yahoo News)       |
|                    |   |  Screener.in)      |   |                    |
+--------------------+   +--------------------+   +--------------------+
```

## Prerequisites

*   Python 3.11 Or Higher
*   Docker And Docker Compose (Optional, For Coolify Deployment)
*   A Telegram Bot Token (From @BotFather)
*   Financial Modeling Prep API Key (Optional, For Enhanced US Data)

## Setup And Installation

### Local Setup

1.  **Clone The Repository**:
    ```bash
    git clone https://github.com/YourUsername/VerifyTrade_Bot.git
    cd VerifyTrade_Bot
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment Variables**:
    Create A `.env` File In The Root Directory And Add Your Tokens (See Example In File).

4.  **Run The Bot**:
    ```bash
    python Sec_Bot.py
    ```

### Docker Deployment (Coolify)

1.  Connect Your Repository To Coolify.
2.  Select The Docker Compose Option.
3.  Add Your Environment Variables (`TELEGRAM_BOT_TOKEN`, `FMP_API_KEY`) In The Coolify Dashboard.
4.  Deploy! The Included `docker-compose.yml` Will Handle The Rest, Including Data Persistence For The Portfolio Database.

## License

This Project Is Licensed Under The MIT License - See The LICENSE File For Details.
