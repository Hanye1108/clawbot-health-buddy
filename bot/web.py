"""趋势图与数据导出网页。

每次她要看图时生成一个临时链接（默认 24 小时有效），链接里带随机令牌，只有拿到链接的人能看。
页面里的图表是服务器直接画好的 SVG，不加载任何外部资源。
"""
import csv
import datetime
import html
import io
import logging
import secrets
import threading

from . import config, records
from .db import now, run

log = logging.getLogger(__name__)


# ---------- 链接 ----------
def create_link(uid):
    run("DELETE FROM report_links WHERE expires_at < ?", (now().isoformat(),))
    token = secrets.token_urlsafe(24)
    expires = now() + datetime.timedelta(hours=config.REPORT_LINK_HOURS)
    run("INSERT INTO report_links (token, user_id, expires_at) VALUES (?,?,?)", (token, uid, expires.isoformat()))
    return f"{config.PUBLIC_URL}/r/{token}", expires


def link_for_tool(uid):
    if not config.WEB_ENABLED or not config.PUBLIC_URL:
        return "管理员还没有开启趋势图网页（需要在 .env 里配置 PUBLIC_URL），请告诉用户暂时看不了图，可以先用文字小结。"
    url, expires = create_link(uid)
    return (f"链接：{url}\n有效期到 {expires.strftime('%m月%d日 %H:%M')}。页面里有体重、热量、运动、喝水的趋势图，"
            "底部可以下载全部记录（CSV，可以用 Excel 打开）。如果微信里打不开，复制链接到手机浏览器里打开。")


def user_for(token):
    rows = run("SELECT user_id, expires_at FROM report_links WHERE token=?", (token,), fetch=True)
    if not rows or rows[0]["expires_at"] < now().isoformat():
        return None
    return rows[0]["user_id"]


# ---------- 画图 ----------
W, H = 640, 270
PAD_L, PAD_R, PAD_T, PAD_B = 62, 14, 18, 40


def _nice(v):
    """把最大值向上取整成好看的刻度（如 1991 → 2100）。"""
    if v <= 0:
        return 1
    import math
    step = 10 ** math.floor(math.log10(v / 2))
    for m in (1, 2, 2.5, 5, 10):
        if v / (step * m) <= 2:
            return math.ceil(v / (step * m)) * step * m
    return v


def _scale(lo, hi, v):
    return PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo or 1))


def _axes(lo, hi, labels, fmt="{:g}"):
    parts = []
    for i in range(3):
        v = lo + (hi - lo) * i / 2
        y = _scale(lo, hi, v)
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                     f'<text x="{PAD_L - 8}" y="{y + 6:.1f}" class="yl">{fmt.format(round(v, 1))}</text>')
    for i, (x, text) in enumerate(labels):
        anchor = "start" if i == 0 and len(labels) > 1 else ("end" if i == len(labels) - 1 and len(labels) > 1
                                                           else "middle")
        x = max(PAD_L, min(x, W - PAD_R)) if anchor != "middle" else x
        parts.append(f'<text x="{x:.1f}" y="{H - 10}" class="xl" text-anchor="{anchor}">{text}</text>')
    return "".join(parts)


def _x(i, n):
    return PAD_L + (W - PAD_L - PAD_R) * (i + 0.5) / n


def bar_chart(dates, values, target=None, unit=""):
    nums = [v for v in values if v]
    if not nums:
        return '<p class="empty">这段时间还没有记录</p>'
    hi = _nice(max(nums + ([target] if target else [])) * 1.05)
    lo = 0
    n = len(dates)
    bw = (W - PAD_L - PAD_R) / n * 0.65
    bars = []
    for i, v in enumerate(values):
        if not v:
            continue
        x, y = _x(i, n), _scale(lo, hi, v)
        bars.append(f'<rect x="{x - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{H - PAD_B - y:.1f}" rx="2" '
                    f'class="bar"><title>{dates[i][5:]}：{v:g}{unit}</title></rect>')
    if target:
        y = _scale(lo, hi, target)
        bars.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" class="target"/>'
                    f'<text x="{W - PAD_R}" y="{y - 7:.1f}" class="tl">目标 {target:g}</text>')
    labels = [(PAD_L, dates[0][5:]), (_x(n // 2, n), dates[n // 2][5:]), (W - PAD_R, dates[-1][5:])]
    return _svg(_axes(lo, hi, labels, "{:.0f}") + "".join(bars))


def line_chart(points, unit=""):
    if not points:
        return '<p class="empty">还没有体重记录</p>'
    import math
    vals = [v for _, v in points]
    lo, hi = math.floor(min(vals) - 0.5), math.ceil(max(vals) + 0.5)
    if (hi - lo) % 2:
        hi += 1
    d0 = datetime.date.fromisoformat(points[0][0])
    span = max((datetime.date.fromisoformat(points[-1][0]) - d0).days, 1)

    def xpos(day):
        return PAD_L + (W - PAD_L - PAD_R) * (datetime.date.fromisoformat(day) - d0).days / span if len(points) > 1 \
            else (W + PAD_L - PAD_R) / 2
    coords = [(xpos(d), _scale(lo, hi, v), d, v) for d, v in points]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _, _) in enumerate(coords))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" class="dot"><title>{d[5:]}：{v:g}{unit}</title></circle>'
                   for x, y, d, v in coords)
    labels = [(coords[0][0], points[0][0][5:])]
    if len(points) > 1:
        labels.append((coords[-1][0], points[-1][0][5:]))
    return _svg(_axes(lo, hi, labels) + f'<path d="{path}" class="line"/>' + dots)


def _svg(body):
    return f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="xMidYMid meet">{body}</svg>'


# ---------- 页面 ----------
CSS = """
:root{--bg:#f7f5f0;--card:#fff;--ink:#2b2a27;--muted:#8a857b;--line:#ece8df;--accent:#5b8c6b;--accent2:#c98b5a}
@media (prefers-color-scheme:dark){:root{--bg:#1c1b19;--card:#262522;--ink:#ece8df;--muted:#9a958b;--line:#35332f;--accent:#7fb08d;--accent2:#d9a477}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:720px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:22px;margin:4px 0 2px}.sub{color:var(--muted);font-size:13px;margin:0 0 18px}
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
@media(min-width:560px){.tiles{grid-template-columns:repeat(4,1fr)}}
.tile{background:var(--card);border-radius:14px;padding:12px 14px}
.tile b{display:block;font-size:20px}.tile span{color:var(--muted);font-size:12px}
section{background:var(--card);border-radius:14px;padding:14px 14px 8px;margin-bottom:14px}
h2{font-size:15px;margin:0 0 6px}svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1.5}.yl{fill:var(--muted);font-size:20px;text-anchor:end}
.xl{fill:var(--muted);font-size:20px}.tl{fill:var(--accent2);font-size:20px;text-anchor:end}
.bar{fill:var(--accent);opacity:.85}.target{stroke:var(--accent2);stroke-dasharray:5 4;stroke-width:1.5}
.line{fill:none;stroke:var(--accent);stroke-width:4;stroke-linejoin:round}.dot{fill:var(--accent)}
.empty{color:var(--muted);font-size:13px;margin:8px 0 14px}
.note{color:var(--muted);font-size:12px}a.btn{display:inline-block;margin:6px 0 10px;padding:9px 16px;
border-radius:10px;background:var(--accent);color:#fff;text-decoration:none}
"""


def render_page(uid, token):
    p = records.get_profile(uid)
    s = records.daily_series(uid, 30)
    target = records.target_info(p).get("每日建议摄入大卡")
    goal = records.water_goal(uid)
    kcal_days = [v for v in s["kcal"] if v]
    w = s["weights"]
    tiles = [
        (f"{w[-1][1]:g} kg" if w else "—", "最近体重"),
        (f"{len(kcal_days)} 天", "近30天有饮食记录"),
        (f"{sum(1 for v in s['exercise'] if v)} 天", "近30天运动"),
        (f"{sum(1 for v in s['water'] if v and v >= goal)} 天", "近30天喝水达标"),
    ]
    name = html.escape(p.get("nickname") or "你")
    tiles_html = "".join(f'<div class="tile"><b>{html.escape(a)}</b><span>{b}</span></div>' for a, b in tiles)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{name}的健康记录</title><style>{CSS}</style></head><body><main>
<h1>{name}的健康记录</h1><p class="sub">生成于 {now().strftime('%Y-%m-%d %H:%M')} · 数字都是估算，仅供参考</p>
<div class="tiles">{tiles_html}</div>
<section><h2>体重（近90天）</h2>{line_chart(w, ' kg')}</section>
<section><h2>每天吃了多少（大卡，近30天）</h2>{bar_chart(s['dates'], s['kcal'], target, ' 大卡')}
<p class="note">只统计记录下来的部分，没记全的日子会偏低。</p></section>
<section><h2>运动（分钟，近30天）</h2>{bar_chart(s['dates'], s['exercise'], None, ' 分钟')}</section>
<section><h2>喝水（毫升，近30天）</h2>{bar_chart(s['dates'], s['water'], goal, ' ml')}</section>
<section><h2>导出数据</h2><a class="btn" href="/r/{token}/export.csv">下载全部记录（CSV）</a>
<p class="note">可以用 Excel 或 WPS 打开。这个链接只有拿到的人能看，到期自动失效，请不要转发。</p></section>
</main></body></html>"""


def export_csv(uid):
    buf = io.StringIO()
    buf.write("﻿")   # 让 Excel 正确识别中文
    csv.writer(buf).writerows(records.export_rows(uid))
    return buf.getvalue()


def register(app):
    from flask import Response, abort

    headers = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}

    @app.route("/r/<token>")
    def report(token):
        uid = user_for(token)
        if not uid:
            abort(404)
        return Response(render_page(uid, token), mimetype="text/html", headers=headers)

    @app.route("/r/<token>/export.csv")
    def export(token):
        uid = user_for(token)
        if not uid:
            abort(404)
        h = dict(headers)
        h["Content-Disposition"] = f"attachment; filename=health-{now().strftime('%Y%m%d')}.csv"
        return Response(export_csv(uid), mimetype="text/csv", headers=h)

    @app.errorhandler(404)
    def not_found(_):
        return Response("<p style='font:16px sans-serif;padding:24px'>链接不存在或已过期，"
                        "在微信里跟小助手说「看看我的趋势图」重新获取。</p>", 404, mimetype="text/html")

    return app


def serve_in_background():
    """ClawBot 通道没有自带网页服务，单独起一个。"""
    if not config.WEB_ENABLED:
        return None
    from flask import Flask
    from werkzeug.serving import WSGIRequestHandler, make_server

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):   # 不把带令牌的链接写进日志
            pass

    app = register(Flask(__name__))
    try:
        server = make_server("0.0.0.0", config.WEB_PORT, app, threaded=True, request_handler=QuietHandler)
    except OSError as e:
        log.error("趋势图网页启动失败（端口 %s）：%s。可在 .env 改 WEB_PORT，或设 WEB_ENABLED=0 关闭", config.WEB_PORT, e)
        return None
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    log.info("趋势图网页已启动：端口 %s，对外地址 %s", config.WEB_PORT, config.PUBLIC_URL or "（未配置 PUBLIC_URL）")
    return server
