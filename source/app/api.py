import requests
import hmac
import hashlib
import base64
from datetime import datetime
import json
import time


def get_signature(message, secret_key):
    mac = hmac.new(bytes(secret_key, encoding='utf-8'), bytes(message, encoding='utf-8'), digestmod=hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')

def getts():
    now = datetime.utcnow()
    return now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

def new_kl(instId, bar, limit=300):
    url = f"https://www.okx.com/api/v5/market/candles?instId={instId}&limit={limit}&bar={bar}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0":
            return res.get("data", [])
        return []
    except:
        return []

def his_kl(instId, bar, after_ts, limit=300):
    url = f"https://www.okx.com/api/v5/market/history-candles?instId={instId}&after={after_ts}&limit={limit}&bar={bar}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0":
            return res.get("data", [])
        return []
    except:
        return []

def get(apikey, secret, passphrase, url, body, is_simulated=False):
    base_url = "https://www.okx.com"
    timestamp = getts()
    method = "GET"
    
    # 处理 Body 占位符
    # GET 请求如果带有参数，通常直接拼接在 url 字符串中，此时 body 传 __EMPTY__
    payload = "" if body == "__EMPTY__" else json.dumps(body)
    
    # 签名信息：timestamp + method + requestPath + body
    sign_msg = timestamp + method + url + payload
    signature = get_signature(sign_msg, secret)
    
    headers = {
        'OK-ACCESS-KEY': apikey,
        'OK-ACCESS-SIGN': signature,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': passphrase,
        'Content-Type': 'application/json',
        'x-simulated-option': '1' if is_simulated else '0'
    }
    
    response = requests.get(base_url + url, headers=headers)
    return response.json()

def post(apikey, secret, passphrase, url, body, is_simulated=False):
    base_url = "https://www.okx.com"
    timestamp = getts()
    method = "POST"
    
    # 处理 Body 占位符
    payload = "" if body == "__EMPTY__" else json.dumps(body)
    
    # 签名信息：timestamp + method + requestPath + body
    sign_msg = timestamp + method + url + payload
    signature = get_signature(sign_msg, secret)
    
    headers = {
        'OK-ACCESS-KEY': apikey,
        'OK-ACCESS-SIGN': signature,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': passphrase,
        'Content-Type': 'application/json',
        'x-simulated-option': '1' if is_simulated else '0'
    }
    
    response = requests.post(base_url + url, data=payload, headers=headers)
    return response.json()

def getpos(apikey, secret, passphrase, instid):
    """获取指定合约的仓位数量 (float)"""
    url = f"/api/v5/account/positions?instType=SWAP&instId={instid}"
    
    # 调用之前定义的 get 函数，body 传入占位符
    res = get(apikey, secret, passphrase, url, "__EMPTY__", is_simulated)
    
    # 逻辑判断：
    # 1. code 为 "0" 表示请求成功
    # 2. data 不为空表示存在持仓信息
    if res.get("code") == "0" and res.get("data"):
        # 获取第一个持仓信息中的 pos 字段
        pos_str = res["data"][0].get("pos", "0")
        return float(pos_str)
    
    # 如果 code 不为 0 或 data 为空，通常意味着当前没有持仓
    return 0.0


def getavailBal(apikey, secret, passphrase):
    """获取 USDT 账户可用余额 (float)"""
    url = "/api/v5/account/balance?ccy=USDT"
    
    # 调用 get 函数，body 传占位符
    res = get(apikey, secret, passphrase, url, "__EMPTY__")
    
    # 按照 OKX 的结构：data[0] -> details[0] -> availBal
    if res.get("code") == "0" and res.get("data"):
        data_item = res["data"][0]
        details = data_item.get("details", [])
        
        if details:
            # 提取第一个 detail 里的可用余额
            avail_bal_str = details[0].get("availBal", "0")
            return float(avail_bal_str)
            
    # 如果请求失败或没有数据，返回 0.0
    return 0.0



def gettotalEq(apikey, secret, passphrase):
    """获取账户总权益 (float)"""
    url = "/api/v5/account/balance?ccy=USDT"
    
    # 调用 get 函数，body 传占位符
    res = get(apikey, secret, passphrase, url, "__EMPTY__")
    
    # 按照 OKX 的结构：data[0] -> totalEq
    if res.get("code") == "0" and res.get("data"):
        # 直接获取 data 数组第一个元素里的 totalEq
        total_eq_str = res["data"][0].get("totalEq", "0")
        return float(total_eq_str)
            
    # 如果请求失败或没有数据，返回 0.0
    return 0.0

def postTaker(apikey, secret, passphrase, instId, tdMode, side, ordType, sz, reduceOnly):
    """发送市价/限价单 (Taker 模式通常指市价)"""
    url = "/api/v5/trade/order"
    
    # 构造请求体，确保 sz 转换为字符串
    body = {
        "instId": instId,
        "tdMode": tdMode,
        "side": side,
        "ordType": ordType,
        "sz": str(sz),
        "reduceOnly": reduceOnly
    }
    
    # 调用之前定义的 post 函数
    res = post(apikey, secret, passphrase, url, body)
    
    # 逻辑判断
    if res.get("code") == "0":
        return "success"
    else:
        # 失败时返回错误前缀和完整的服务器响应
        return f"e: {res}"

def postMaker(apikey, secret, passphrase, instId, tdMode, side, ordType, px, sz, clOrdId, reduceOnly):
    """发送限价单 (Maker 模式)"""
    url = "/api/v5/trade/order"
    
    # 构造请求体，确保 px 和 sz 均转换为字符串
    body = {
        "instId": instId,
        "tdMode": tdMode,
        "side": side,
        "ordType": ordType,
        "px": str(px),
        "sz": str(sz),
        "clOrdId": clOrdId,
        "reduceOnly": reduceOnly
    }
    
    # 调用之前定义的 post 函数
    res = post(apikey, secret, passphrase, url, body)
    
    # 逻辑判断
    if res.get("code") == "0":
        return "success"
    else:
        # 失败时返回错误前缀和完整的服务器响应 JSON
        return f"e: {res}"


def postalgotp(apikey, secret, passphrase, instId, tdMode, side, ordType, closeFractional, sz, tpTriggerPx, tpOrdPx, algoClOrdId):
    """发送策略止盈单 (Algo Order)"""
    url = "/api/v5/trade/order-algo"
    
    # 构造请求体
    # sz, tpTriggerPx, tpOrdPx 强制转为字符串
    # closeFractional 保持 bool 类型
    body = {
        "instId": instId,
        "tdMode": tdMode,
        "side": side,
        "ordType": ordType,
        "closeFractional": closeFractional,
        "sz": str(sz),
        "tpTriggerPx": str(tpTriggerPx),
        "tpOrdPx": str(tpOrdPx),
        "algoClOrdId": algoClOrdId
    }
    
    # 调用之前定义的 post 函数
    res = post(apikey, secret, passphrase, url, body)
    
    # 逻辑判断
    if res.get("code") == "0":
        return "success"
    else:
        # 失败时返回错误前缀和完整的服务器响应 JSON
        return f"e: {res}"

def postalgosl(apikey, secret, passphrase, instId, tdMode, side, ordType, closeFractional, sz, slTriggerPx, slOrdPx, algoClOrdId):
    """发送策略止损单 (Algo Stop Loss)"""
    url = "/api/v5/trade/order-algo"
    
    # 构造请求体
    # sz, slTriggerPx, slOrdPx 强制转为字符串
    # closeFractional 保持 bool 类型
    body = {
        "instId": instId,
        "tdMode": tdMode,
        "side": side,
        "ordType": ordType,
        "closeFractional": closeFractional,
        "sz": str(sz),
        "slTriggerPx": str(slTriggerPx),
        "slOrdPx": str(slOrdPx),
        "algoClOrdId": algoClOrdId
    }
    
    # 调用之前定义的 post 函数
    res = post(apikey, secret, passphrase, url, body)
    
    # 逻辑判断
    if res.get("code") == "0":
        return "success"
    else:
        # 失败时返回错误前缀和完整的服务器响应 JSON
        return f"e: {res}"

def postcancelaigo(apikey, secret, passphrase, instId, *algoClOrdIds):
    """
    批量撤销策略单 (Algo Orders)
    *algoClOrdIds: 可以传入一个或多个自定义 ID
    """
    url = "/api/v5/trade/cancel-algos"
    
    # 按照请求示例构造 body 列表
    # 每个 ID 都要和 instId 配对组成一个 dict
    body = [
        {"instId": instId, "algoClOrdId": acoid} 
        for acoid in algoClOrdIds
    ]
    
    # 调用之前定义的 post 函数
    # 注意：这里的 body 是一个 list，post 函数内部的 json.dumps 会处理好格式
    res = post(apikey, secret, passphrase, url, body)
    
    # 逻辑判断
    if res.get("code") == "0":
        return "success"
    else:
        # 失败时返回错误前缀和完整的服务器响应 JSON
        return f"e: {res}"


def postsetlev(apikey, secret, passphrase, instId, mgnMode, lever):
    """设定指定合约的杠杆倍数"""
    url = "/api/v5/account/set-leverage"
    
    # 构造请求体，确保 lever 转换为字符串
    body = {
        "instId": instId,
        "mgnMode": mgnMode,
        "lever": str(lever)
    }
    
    # 调用之前定义的 post 函数
    res = post(apikey, secret, passphrase, url, body)
    
    # 逻辑判断
    if res.get("code") == "0":
        return "success"
    else:
        # 失败时返回错误前缀和完整响应
        return f"e: {res}"
