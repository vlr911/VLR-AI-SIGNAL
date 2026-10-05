from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List
from pathlib import Path
from urllib.request import urlopen
import json
import math
import time


BASE = Path(__file__).parent

app = FastAPI(
    title="VLR AI Signal Engine",
    version="0.3.0"
)

# Allow GitHub Pages + Render frontend to communicate with the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


class AnalyzeRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    candles: List[Candle] = Field(
        min_length=55,
        max_length=1000
    )


def ema(values, period):
    if not values:
        return []

    alpha = 2 / (period + 1)
    out = [values[0]]

    for value in values[1:]:
        out.append(
            alpha * value +
            (1 - alpha) * out[-1]
        )

    return out


def fetch_xaus_points(hours=24):
    """
    Fetch XAU/USD price points from XAUS.
    XAUS returns approximately 2-minute price samples.
    """

    url = (
        "https://xaus.com/api/v1/intraday"
        "?symbol=xau&hours="
        + str(hours)
    )

    try:
        with urlopen(url, timeout=15) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"XAUS data fetch failed: {exc}"
        )

    points = data.get("points", [])

    if not points:
        raise HTTPException(
            status_code=502,
            detail="XAUS returned no price points."
        )

    return data, points


def build_m15_candles(points):
    """
    Convert XAUS 2-minute price samples
    into 15-minute OHLC candles.
    """

    buckets = {}

    for point in points:
        try:
            timestamp = int(point["t"])
            price = float(point["p"])
        except (KeyError, TypeError, ValueError):
            continue

        # Align timestamp to a 15-minute candle
        bucket_time = (timestamp // 900) * 900

        if bucket_time not in buckets:
            buckets[bucket_time] = []

        buckets[bucket_time].append(
            (timestamp, price)
        )

    candles = []

    for bucket_time in sorted(buckets.keys()):
        samples = sorted(
            buckets[bucket_time],
            key=lambda x: x[0]
        )

        prices = [price for _, price in samples]

        if len(prices) < 2:
            continue

        candle = Candle(
            time=bucket_time,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=0
        )

        candles.append(candle)

    return candles


def analyze_candles(candles, symbol="XAUUSD", timeframe="M15"):
    if len(candles) < 55:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough candles. Got {len(candles)}, need at least 55."
        )

    cs = candles

    closes = [c.close for c in cs]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)

    last = cs[-1]

    prior = cs[-9:-1]

    swing_hi = max(
        c.high for c in prior
    )

    swing_lo = min(
        c.low for c in prior
    )

    bullish = (
        e20[-1] > e50[-1]
        and last.close > last.open
        and last.close > swing_hi
    )

    bearish = (
        e20[-1] < e50[-1]
        and last.close < last.open
        and last.close < swing_lo
    )

    if bullish:
        side = "BUY"
    elif bearish:
        side = "SELL"
    else:
        side = "WAIT"

    # ATR-style volatility estimate
    true_ranges = []

    start_index = max(1, len(cs) - 14)

    for i in range(start_index, len(cs)):
        current = cs[i]
        previous = cs[i - 1]

        tr = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close)
        )

        true_ranges.append(tr)

    atr = (
        sum(true_ranges) / len(true_ranges)
        if true_ranges
        else 0.0
    )

    risk = max(
        atr * 1.25,
        0.5
    )

    entry = last.close

    if side == "BUY":

        sl = entry - risk

        tps = [
            entry + risk,
            entry + 2 * risk,
            entry + 3 * risk
        ]

        reason = (
            "EMA20 above EMA50 and bullish "
            "M15 candle closed above the prior "
            "8-candle swing high."
        )

    elif side == "SELL":

        sl = entry + risk

        tps = [
            entry - risk,
            entry - 2 * risk,
            entry - 3 * risk
        ]

        reason = (
            "EMA20 below EMA50 and bearish "
            "M15 candle closed below the prior "
            "8-candle swing low."
        )

    else:

        sl = None
        tps = []

        reason = (
            "Trend and breakout conditions "
            "are not aligned; no trade signal."
        )

    score = 80 if side != "WAIT" else 0

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "signal": side,
        "score": score,

        "entry_zone": (
            [
                round(entry - 0.15, 2),
                round(entry + 0.15, 2)
            ]
            if side != "WAIT"
            else None
        ),

        "stop_loss": (
            round(sl, 2)
            if sl is not None
            else None
        ),

        "take_profits": [
            round(x, 2)
            for x in tps
        ],

        "atr_estimate": round(atr, 3),

        "candle_time": last.time,

        "reason": reason,

        "mode": (
            "XAUS live price feed + "
            "M15 aggregation + rules prototype; "
            "not financial advice; verify feed, "
            "spread and broker contract specs"
        )
    }


@app.get("/")
def home():
    return FileResponse(
        BASE / "index.html"
    )


@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service": "VLR AI Signal Engine",
        "mode": "XAUS live XAU/USD feed + M15 analysis"
    }


@app.get("/api/live-analyze")
def live_analyze():

    data, points = fetch_xaus_points(
        hours=24
    )

    candles = build_m15_candles(
        points
    )

    result = analyze_candles(
        candles,
        symbol="XAUUSD",
        timeframe="M15"
    )

    result["data_source"] = "XAUS"
    result["source_symbol"] = data.get(
        "symbol",
        "xau"
    )

    result["source_interval_seconds"] = data.get(
        "interval_seconds",
        120
    )

    result["source_count"] = data.get(
        "count",
        len(points)
    )

    result["m15_candle_count"] = len(
        candles
    )

    result["feed_age_seconds"] = data.get(
        "age_seconds"
    )

    result["data_state"] = data.get(
        "data_state"
    )

    result["last_price"] = round(
        float(points[-1]["p"]),
        2
    )

    return result


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):

    return analyze_candles(
        req.candles,
        symbol=req.symbol,
        timeframe=req.timeframe
    )
