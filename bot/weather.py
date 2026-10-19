"""和风天气（新版 v1 接口：先用城市搜索拿经纬度，再按经纬度查天气）。"""
import json

import requests

from . import config


def qw_get(path, params=None):
    r = requests.get(config.QWEATHER_HOST + path, params=params,
                     headers={"X-QW-Api-Key": config.QWEATHER_KEY}, timeout=10)
    return r.json()


def _pct(x):
    return f"{round(x * 100)}%"


def _brief(period):
    return {
        "天气": period["condition"]["text"],
        "最低": period["temperatureMin"]["value"],
        "最高": period["temperatureMax"]["value"],
        "降水概率": _pct(period["precipitation"]["probability"]),
        "降水量mm": period["precipitation"]["amount"]["value"],
        "风力": f'{period["wind"]["scale"]}级',
    }


def lookup_city(city, province=""):
    params = {"location": city, "range": "cn", "number": 1}
    if province:
        params["adm"] = province
    geo = qw_get("/geo/v2/city/lookup", params)
    if geo.get("code") != "200" or not geo.get("location"):
        return None
    return geo["location"][0]


def get_weather(city, province=""):
    loc = lookup_city(city, province)
    if not loc:
        return f"没有找到城市：{province}{city}"
    lat, lon = round(float(loc["lat"]), 2), round(float(loc["lon"]), 2)

    now = qw_get(f"/weather/v1/current/{lat}/{lon}", {"localTime": "true"})
    fc = qw_get(f"/weather/v1/daily/{lat}/{lon}", {"days": 3, "localTime": "true"})
    if "condition" not in now:
        return "天气接口出错：" + json.dumps(now, ensure_ascii=False)[:200]

    result = {
        "城市": f'{loc["adm1"]}{loc["adm2"]}{loc["name"]}',
        "现在": {
            "天气": now["condition"]["text"],
            "温度": now["temperature"]["value"],
            "体感": now["feelsLike"]["value"],
            "湿度": _pct(now["humidity"]),
            "风力": f'{now["wind"]["scale"]}级',
            "降水强度mm每小时": now["precipitation"]["intensity"]["value"],
        },
        "未来几天": [
            {"日期": d["forecastStartTime"][:10],
             "白天": _brief(d["daytime"]), "夜间": _brief(d["nighttime"])}
            for d in fc.get("days", [])
        ],
    }
    return json.dumps(result, ensure_ascii=False)
