"""Radar: Mais Vendidos ML → Telegram (com preço via /products/{id}/items)."""
import os, json, html, datetime as dt
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

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/1.1", "Accept": "application/json"})


def headers_ml():
    cid, sec = os.getenv("ML_CLIENT_ID"), os.getenv("ML_CLIENT_SECRET")
    if not (cid and sec):
        print("ERRO: secrets ML ausentes")
        return {}
    r = S.post(f"{API}/oauth/token", data={
        "grant_type": "client_credentials", "client_id": cid, "client_secret": sec
    }, timeout=20)
    r.raise_for_status()
    print("Token ML OK")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def link_afiliado(url):
    sep = "&" if "?" in url else "?"
    return url + sep + AFF_PARAMS.format(id=AFF_ID)


def montar_produto(pid, tipo, h):
    """Resolve título, preço, foto e link a partir do ID do highlight."""
    titulo, foto = "", ""
    preco, frete, item_id = 0, False, None

    # 1) Dados do produto (nome + foto)
    try:
        rp = S.get(f"{API}/products/{pid}", headers=h, timeout=15)
        if rp.status_code == 200:
            d = rp.json()
            titulo = d.get("name") or d.get("title") or ""
            pics = d.get("pictures") or []
            if pics:
                foto = pics[0].get("url") or pics[0].get("secure_url") or ""
            foto = foto.replace("http://", "https://")
    except Exception as e:
        print(f"  products/{pid}:", e)

    # 2) Preço via /products/{id}/items (funciona com nosso token)
    try:
        ri = S.get(f"{API}/products/{pid}/items", headers=h, timeout=15)
        if ri.status_code == 200:
            results = ri.json().get("results") or []
            if results:
                best = min(results, key=lambda x: x.get("price") or 999999)
                preco = float(best.get("price") or 0)
                item_id = best.get("item_id")
                frete = (best.get("shipping") or {}).get("free_shipping", False)
    except Exception as e:
        print(f"  products/{pid}/items:", e)

    # 3) Fallback se for ITEM puro
    if preco <= 0 and (tipo == "ITEM" or pid.startswith("MLB")):
        try:
            ri = S.get(f"{API}/items/{pid}", headers=h, timeout=15)
            if ri.status_code == 200:
                d = ri.json()
                titulo = titulo or d.get("title") or ""
                preco = float(d.get("price") or 0)
                item_id = pid
                frete = (d.get("shipping") or {}).get("free_shipping", False)
                if not foto and d.get("thumbnail"):
                    foto = d["thumbnail"].replace("http://", "https://")
        except Exception:
            pass

    if not titulo or preco <= 0:
        return None

    # Link público do produto/item
    if item_id:
        url = f"https://www.mercadolivre.com.br/p/{pid}" if tipo == "PRODUCT" else f"https://produto.mercadolivre.com.br/{item_id}"
        # formato mais estável:
        url = f"https://www.mercadolivre.com.br/p/{pid}"
    else:
        url = f"https://www.mercadolivre.com.br/p/{pid}"

    return {
        "id": pid,
        "titulo": titulo,
        "preco": preco,
        "imagem": foto,
        "frete_gratis": frete,
        "link": link_afiliado(url),
    }


def mais_vendidos(cat_id, nome, h, limit=3):
    try:
        r = S.get(f"{API}/highlights/MLB/category/{cat_id}", headers=h, timeout=20)
        print(f"Highlights {nome}: {r.status_code}")
        if r.status_code != 200:
            return []
        content = r.json().get("content", [])[:limit]
    except Exception as e:
        print(f"Highlights {nome} erro:", e)
        return []

    prods = []
    for item in content:
        pid = item.get("id")
        tipo = item.get("type", "PRODUCT")
        if not pid:
            continue
        p = montar_produto(pid, tipo, h)
        if p:
            p["categoria"] = nome
            prods.append(p)
            print(f"  OK R$ {p['preco']:.2f} — {p['titulo'][:55]}")
        else:
            print(f"  FALHOU detalhe: {pid} ({tipo})")
    return prods


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def postar(p):
    texto = (
        f"🔥 <b>Mais vendido — {html.escape(p.get('categoria', ''))}</b>\n\n"
        f"📦 {html.escape(p['titulo'][:130])}\n"
        f"💰 <b>{preco_br(p['preco'])}</b>\n"
        + ("🚚 Frete grátis\n" if p.get("frete_gratis") else "")
        + f"\n🔗 <a href=\"{html.escape(p['link'])}\">Ver oferta no Mercado Livre</a>\n\n"
        "<i>Link de afiliado. Preços podem variar.</i>"
    )
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado")
        return False
    if p.get("imagem"):
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", timeout=30,
                   data={"chat_id": TG_CHAT, "photo": p["imagem"], "caption": texto, "parse_mode": "HTML"})
    else:
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", timeout=30,
                   data={"chat_id": TG_CHAT, "text": texto, "parse_mode": "HTML"})
    print("Telegram:", r.status_code, r.text[:180])
    return r.ok


def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    h = headers_ml()
    if not h:
        return

    ranking, vistos = [], set()
    for cat_id, nome in CATEGORIAS.items():
        for p in mais_vendidos(cat_id, nome, h, limit=3):
            if p["id"] in vistos:
                continue
            vistos.add(p["id"])
            ranking.append(p)

    print("Total produtos:", len(ranking))
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": ranking[:20]},
                                 ensure_ascii=False, indent=2), encoding="utf-8")

    if not ranking:
        print("Nada a postar")
        return
    if not (TG_TOKEN and TG_CHAT):
        print("Falta Telegram")
        return
    if not (8 <= agora.hour < 23):
        print("Fora do horário")
        return

    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        estado = json.loads(STATE.read_text(encoding="utf-8") or "{}")
    except Exception:
        estado = {}
    limite = agora - dt.timedelta(days=REPETIR_APOS_DIAS)
    estado = {k: v for k, v in estado.items() if dt.datetime.fromisoformat(v) > limite}

    enviados = 0
    for p in ranking:
        if enviados >= MAX_POSTS:
            break
        if p["id"] in estado:
            continue
        if postar(p):
            estado[p["id"]] = agora.isoformat()
            enviados += 1

    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
