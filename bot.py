"""Radar de ofertas: coleta tendências, escolhe produtos, atualiza o site e posta no Telegram."""
import os, json, html, datetime as dt
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
import requests

AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
# Confirme no painel de afiliados do ML qual parâmetro seu link deve ter.
AFF_PARAMS = os.getenv("ML_AFFILIATE_PARAMS", "matt_tool={id}")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
MAX_POSTS = int(os.getenv("MAX_POSTS", "3"))
REPETIR_APOS_DIAS = 7
API = "https://api.mercadolibre.com"
ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")


def headers_ml():
    cid, sec = os.getenv("ML_CLIENT_ID"), os.getenv("ML_CLIENT_SECRET")
    if not (cid and sec):
        return {}
    try:
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, timeout=15)
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    except Exception as e:
        print("Aviso: token ML falhou:", e)
        return {}


def tendencias_ml(h):
    r = requests.get(f"{API}/trends/MLB", headers=h, timeout=15)
    r.raise_for_status()
    return [t["keyword"].strip().lower() for t in r.json()]


def tendencias_google():
    r = requests.get("https://trends.google.com/trending/rss?geo=BR", timeout=15,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return [i.findtext("title").strip().lower() for i in ET.fromstring(r.content).iter("item")]


def coletar(nome, fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"Aviso: fonte {nome} falhou:", e)
        return []


def melhor_produto(termo, h):
    try:
        r = requests.get(f"{API}/sites/MLB/search", headers=h, timeout=15,
                         params={"q": termo, "limit": 20, "condition": "new"})
        r.raise_for_status()
    except Exception as e:
        print("Aviso: busca falhou para", termo, e)
        return None
    itens = [i for i in r.json().get("results", []) if i.get("price") and i.get("available_quantity", 0) > 0]
    if not itens:
        return None
    i = max(itens, key=lambda x: (x.get("sold_quantity") or 0, x.get("shipping", {}).get("free_shipping", False)))
    foto = (i.get("thumbnail") or "").replace("http://", "https://").replace("-I.jpg", "-O.jpg")
    return {"id": i["id"], "titulo": i["title"], "preco": i["price"], "imagem": foto,
            "vendidos": i.get("sold_quantity") or 0,
            "frete_gratis": i.get("shipping", {}).get("free_shipping", False),
            "link": link_afiliado(i["permalink"])}


def link_afiliado(url):
    sep = "&" if "?" in url else "?"
    return url + sep + AFF_PARAMS.format(id=AFF_ID)


def montar_ranking(h):
    ml, goog = coletar("Mercado Livre", tendencias_ml, h), coletar("Google Trends", tendencias_google)
    cand = {}
    for pos, t in enumerate(ml):
        cand[t] = {"termo": t, "score": 50 - pos, "fontes": ["Mercado Livre"]}
    for g in goog:
        for t, c in cand.items():
            if g in t or t in g:
                c["score"] += 30
                c["fontes"].append("Google Trends")
        if not ml:  # ML fora do ar: usa o Google como reserva
            cand.setdefault(g, {"termo": g, "score": 20, "fontes": ["Google Trends"]})
    ranking = sorted(cand.values(), key=lambda c: -c["score"])[:20]
    for c in ranking:
        c["produto"] = melhor_produto(c["termo"], h)
    return [c for c in ranking if c["produto"]]


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def postar(item):
    p = item["produto"]
    texto = (f"🔥 <b>Em alta: {html.escape(item['termo'])}</b>\n\n"
             f"📦 {html.escape(p['titulo'])}\n💰 <b>{preco_br(p['preco'])}</b>\n"
             + ("🚚 Frete grátis\n" if p["frete_gratis"] else "")
             + f"\n🔗 <a href=\"{html.escape(p['link'])}\">Ver oferta no Mercado Livre</a>\n\n"
             "<i>Link de afiliado. Preços e estoque podem variar a qualquer momento.</i>")
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", timeout=20,
                      data={"chat_id": TG_CHAT, "photo": p["imagem"], "caption": texto, "parse_mode": "HTML"})
    print("Telegram:", r.status_code, r.text[:120])
    return r.ok


def main():
    agora = dt.datetime.now(TZ)
    ranking = montar_ranking(headers_ml())
    if not ranking:
        print("Nenhum dado coletado; mantendo o site como está.")
        return
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": ranking},
                                 ensure_ascii=False, indent=1), encoding="utf-8")

    if not (TG_TOKEN and TG_CHAT) or not (8 <= agora.hour < 22):
        print("Sem Telegram configurado ou fora do horário de postagem (8h–22h).")
        return
    estado = json.loads(STATE.read_text() or "{}")
    limite = agora - dt.timedelta(days=REPETIR_APOS_DIAS)
    estado = {k: v for k, v in estado.items() if dt.datetime.fromisoformat(v) > limite}
    enviados = 0
    for item in ranking:
        if enviados >= MAX_POSTS:
            break
        pid = item["produto"]["id"]
        if pid in estado:
            continue
        if postar(item):
            estado[pid] = agora.isoformat()
            enviados += 1
    STATE.write_text(json.dumps(estado, indent=1))


if __name__ == "__main__":
    main()
