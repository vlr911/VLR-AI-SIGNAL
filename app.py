from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List
from pathlib import Path
import math

BASE=Path(__file__).parent
app=FastAPI(title="Velora Signal Engine", version="0.2.0")
app.mount("/static",StaticFiles(directory=BASE/"static"),name="static")

class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float=0

class AnalyzeRequest(BaseModel):
    symbol: str="XAUUSD"
    timeframe: str="M15"
    candles: List[Candle]=Field(min_length=55,max_length=1000)

def ema(values, period):
    alpha=2/(period+1)
    out=[values[0]]
    for v in values[1:]:
        out.append(alpha*v+(1-alpha)*out[-1])
    return out

@app.get("/")
def home():
    return FileResponse(BASE/"static"/"index.html")

@app.get("/api/health")
def health():
    return {"ok":True,"service":"Velora Signal Engine","mode":"analysis API; connect a verified feed for live data"}

@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    cs=req.candles
    closes=[c.close for c in cs]
    e20,e50=ema(closes,20),ema(closes,50)
    last,prev=cs[-1],cs[-2]
    prior=cs[-9:-1]
    swing_hi=max(c.high for c in prior)
    swing_lo=min(c.low for c in prior)
    bullish=e20[-1]>e50[-1] and last.close>last.open and last.close>swing_hi
    bearish=e20[-1]<e50[-1] and last.close<last.open and last.close<swing_lo
    side="BUY" if bullish else "SELL" if bearish else "WAIT"
    atr=sum(max(c.high-c.low,abs(c.high-cs[i-1].close),abs(c.low-cs[i-1].close)) for i,c in enumerate(cs[-14:],start=len(cs)-14))/14
    risk=max(atr*1.25,0.5)
    entry=last.close
    if side=="BUY":
        sl=entry-risk; tps=[entry+risk,entry+2*risk,entry+3*risk]
        reason="EMA20 above EMA50; bullish candle closed above prior 8-bar swing high." 
    elif side=="SELL":
        sl=entry+risk; tps=[entry-risk,entry-2*risk,entry-3*risk]
        reason="EMA20 below EMA50; bearish candle closed below prior 8-bar swing low."
    else:
        sl=None;tps=[];reason="Trend and breakout conditions are not aligned; no trade signal."
    score=80 if side!="WAIT" else 0
    return {"symbol":req.symbol,"timeframe":req.timeframe,"signal":side,"score":score,
            "entry_zone":[round(entry-0.15,2),round(entry+0.15,2)] if side!="WAIT" else None,
            "stop_loss":round(sl,2) if sl is not None else None,
            "take_profits":[round(x,2) for x in tps],"atr_estimate":round(atr,3),
            "candle_time":last.time,"reason":reason,
            "mode":"rules prototype; not financial advice; verify feed, spread and broker contract specs"}
