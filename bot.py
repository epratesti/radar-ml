"""Radar de ofertas: Mais Vendidos do ML + postagem no Telegram."""
import os
import json
import html
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
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

CATEGORIAS = {
    "MLB1051": "Celulares",
    "MLB1430": "Calçados e Roupas",
    "MLB1144": "Games",
    "MLB1000": "Eletrônicos",
    "MLB1276": "Esportes e Fitness",
    "MLB1648": "Informática",
    "MLB1246": "Beleza",
    "MLB5726": "Eletrodomésticos",
    "MLB1132": "Brinquedos",
    "MLB1574": "Casa",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "radar-ml/1.0", "Accept": "application/json"})


def headers_ml():
    cid, sec = os.getenv("ML_CLIENT_ID"), os.getenv("ML_CLIENT_SECRET")
    if not (cid and sec):
        print("ERRO: ML_CLIENT_ID ou ML_CLIENT_SECRET ausente")
        return {}
    try:
        r = SESSION.post(
            f"{API}/oauth/token",
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec},
            timeout=20,
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        print("Token ML OK")
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:
        print("ERRO token ML:", e)
        return {}


def link_afiliado(url):
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + AFF_PARAMS.format(id=AFF_ID)


def detalhe_produto(pid, h):
    for endpoint in (f"{API}/products/{pid}", f"{API}/items/{pid}"):
        try:
            r = SESSION.get(endpoint, headers=h, timeout=15)
            if r.status_code != 200:
                continue
            d = r.json()
            titulo = d.get("name") or d.get("title") or ""
            preco = 0
            if d.get("buy_box_winner") and d["buy_box_winner"].get("price"):
                preco = float(d["buy_box_winner"]["price"])
            elif d.get("price"):
                preco = float(d["price"])
            permalink = d.get("permalink") or f"https://www.mercadolivre.com.br/p/{pid}"
            foto = ""
            pics = d.get("pictures") or []
            if pics and isinstance(pics, list):
                foto = pics[0].get("secure_url") or pics[0].get("url") or ""
            elif d.get("thumbnail"):
                foto = d["thumbnail"]
            foto = (foto or "").replace("http://", "https://").replace("-I.jpg", "-O.jpg")
            if titulo and preco > 0:
                return {
                    "id": pid,
                    "titulo": titulo,
                    "preco": preco,
                    "imagem": foto,
                    "link": link_afiliado(permalink),
                    "frete_gratis": True,
                }
        except Exception as e:
            print(f"  detalhe {pid} falhou:", e)
    return None


def mais_vendidos(cat_id, nome, h, limit=4):
    try:
        r = SESSION.get(f"{API}/highlights/MLB/category/{cat_id}", headers=h, timeout=20)
        print(f"Highlights {nome} ({cat_id}): {r.status_code}")
        if r.status_code != 200:
            print("  body:", r.text[:120])
            return []
        content = r.json().get("content", [])[:limit]
    except Exception as e:
        print(f"Highlights {nome} erro:", e)
        return []

    prods = []
    for item in content:
        pid = item.get("id")
        if not pid:
            continue
        p = detalhe_produto(pid, h)
        if p:
            p["categoria"] = nome
            p["fonte"] = "Mais Vendidos"
            prods.append(p)
            print(f"  OK: {p['titulo'][:50]} — R$ {p['preco']}")
    return prods


def montar_ranking(h):
    ranking = []
    vistos = set()
    for cat_id, nome in CATEGORIAS.items():
        for p in mais_vendidos(cat_id, nome, h, limit=3):
            if p["id"] in vistos:
                continue
            vistos.add(p["id"])
            ranking.append({
                "termo": p["titulo"][:70],
                "score": 100,
                "fontes": ["Mais Vendidos", nome],
                "produto": p,
            })
    print(f"Total produtos no ranking: {len(ranking)}")
    return ranking


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def postar(item):
    p = item["produto"]
    fontes = ", ".join(item.get("fontes", []))
    texto = (
        f"🔥 <b>Mais vendido</b>\n\n"
        f"📦 {html.escape(p['titulo'][:130])}\n"
        f"💰 <b>{preco_br(p['preco'])}</b>\n"
        f"🚚 Frete grátis\n"
        f"📌 {html.escape(p.get('categoria', ''))}\n\n"
        f"🔗 <a href=\"{html.escape(p['link'])}\">Ver oferta no Mercado Livre</a>\n\n"
        "<i>Link de afiliado. Preços podem variar.</i>"
    )
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado")
        return False

    if p.get("imagem"):
        r = SESSION.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
            data={"chat_id": TG_CHAT, "photo": p["imagem"], "caption": texto, "parse_mode": "HTML"},
            timeout=30,
        )
    else:
        r = SESSION.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": texto, "parse_mode": "HTML"},
            timeout=30,
        )
    print("Telegram:", r.status_code, r.text[:200])
    return r.ok


def main():
    agora = dt.datetime.now(TZ)
    print(f"Iniciando {agora.isoformat()}")
    h = headers_ml()
    if not h:
        return

    ranking = montar_ranking(h)
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    TRENDS.write_text(
        json.dumps({"atualizado": agora.isoformat(), "itens": ranking[:15]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not ranking:
        print("Nenhum produto — nada a postar")
        return
    if not (TG_TOKEN and TG_CHAT):
        print("Falta TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID")
        return
    if not (8 <= agora.hour < 23):
        print("Fora do horário 8h–23h")
        return

    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        estado = json.loads(STATE.read_text(encoding="utf-8") or "{}")
    except Exception:
        estado = {}
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

    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Postagens enviadas: {enviados}")


if __name__ == "__main__":
    main()
