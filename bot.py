"""Radar de ofertas: coleta Mais Vendidos + tendências, escolhe produtos e posta no Telegram."""
import os
import json
import html
import datetime as dt
from pathlib import Path
from xml.etree import ElementTree as ET
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
}

def headers_ml():
    cid, sec = os.getenv("ML_CLIENT_ID"), os.getenv("ML_CLIENT_SECRET")
    if not (cid and sec):
        print("Aviso: ML_CLIENT_ID ou ML_CLIENT_SECRET não configurados")
        return {}
    try:
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": sec
        }, timeout=15)
        r.raise_for_status()
        print("Token ML gerado com sucesso")
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    except Exception as e:
        print("Aviso: token ML falhou:", e)
        return {}

def tendencias_ml(h):
    try:
        r = requests.get(f"{API}/trends/MLB", headers=h, timeout=15)
        r.raise_for_status()
        return [t["keyword"].strip().lower() for t in r.json()]
    except Exception as e:
        print("Aviso: tendências ML falhou:", e)
        return []

def tendencias_google():
    try:
        r = requests.get("https://trends.google.com/trending/rss?geo=BR", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return [i.findtext("title").strip().lower() for i in ET.fromstring(r.content).iter("item")]
    except Exception as e:
        print("Aviso: Google Trends falhou:", e)
        return []

def mais_vendidos_categoria(cat_id, h, limit=3):
    try:
        r = requests.get(f"{API}/highlights/MLB/category/{cat_id}", headers=h, timeout=15)
        r.raise_for_status()
        content = r.json().get("content", [])[:limit]
    except Exception as e:
        print(f"Aviso: highlights {cat_id} falhou:", e)
        return []

    produtos = []
    for item in content:
        pid = item.get("id")
        if not pid:
            continue
        try:
            det = None
            for endpoint in (f"{API}/products/{pid}", f"{API}/items/{pid}"):
                try:
                    rd = requests.get(endpoint, headers=h, timeout=12)
                    if rd.status_code == 200:
                        det = rd.json()
                        break
                except Exception:
                    continue
            if not det:
                continue

            titulo = det.get("name") or det.get("title") or ""
            preco = (det.get("buy_box_winner") or {}).get("price") or det.get("price") or 0
            permalink = det.get("permalink") or f"https://produto.mercadolivre.com.br/{pid}"
            thumbs = det.get("pictures") or det.get("thumbnail") or []
            if isinstance(thumbs, list) and thumbs:
                foto = thumbs[0].get("url") or thumbs[0].get("secure_url") or ""
            elif isinstance(thumbs, str):
                foto = thumbs
            else:
                foto = ""
            foto = foto.replace("http://", "https://").replace("-I.jpg", "-O.jpg")

            if titulo and preco:
                produtos.append({
                    "id": pid,
                    "titulo": titulo,
                    "preco": float(preco),
                    "imagem": foto,
                    "vendidos": det.get("sold_quantity") or 0,
                    "frete_gratis": True,
                    "link": link_afiliado(permalink),
                    "categoria": CATEGORIAS.get(cat_id, cat_id),
                    "fonte": "Mais Vendidos",
                })
        except Exception as e:
            print(f"Aviso: detalhe {pid} falhou:", e)
    return produtos

def melhor_produto_busca(termo, h):
    try:
        r = requests.get(f"{API}/sites/MLB/search", headers=h, timeout=15,
                         params={"q": termo, "limit": 15, "condition": "new"})
        r.raise_for_status()
    except Exception as e:
        print("Aviso: busca falhou para", termo, e)
        return None

    itens = [i for i in r.json().get("results", []) if i.get("price") and i.get("available_quantity", 0) > 0]
    if not itens:
        return None
    i = max(itens, key=lambda x: (x.get("sold_quantity") or 0, x.get("shipping", {}).get("free_shipping", False)))
    foto = (i.get("thumbnail") or "").replace("http://", "https://").replace("-I.jpg", "-O.jpg")
    return {
        "id": i["id"],
        "titulo": i["title"],
        "preco": float(i["price"]),
        "imagem": foto,
        "vendidos": i.get("sold_quantity") or 0,
        "frete_gratis": i.get("shipping", {}).get("free_shipping", False),
        "link": link_afiliado(i["permalink"]),
        "categoria": "Tendência",
        "fonte": "Busca",
    }

def link_afiliado(url):
    sep = "&" if "?" in url else "?"
    return url + sep + AFF_PARAMS.format(id=AFF_ID)

def montar_ranking(h):
    candidatos = []
    for cat_id, nome in CATEGORIAS.items():
        for p in mais_vendidos_categoria(cat_id, h, limit=3):
            candidatos.append({
                "termo": p["titulo"][:60],
                "score": 80 + min(p["vendidos"] // 1000, 20),
                "fontes": ["Mais Vendidos", nome],
                "produto": p,
            })

    ml = tendencias_ml(h)
    goog = tendencias_google()
    for pos, t in enumerate(ml[:12]):
        score = 50 - pos
        if any(g in t or t in g for g in goog):
            score += 25
        prod = melhor_produto_busca(t, h)
        if prod:
            candidatos.append({"termo": t, "score": score, "fontes": ["Tendência ML"], "produto": prod})

    vistos = set()
    ranking = []
    for c in sorted(candidatos, key=lambda x: -x["score"]):
        pid = c["produto"]["id"]
        if pid in vistos:
            continue
        vistos.add(pid)
        ranking.append(c)
        if len(ranking) >= 25:
            break
    return ranking

def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def postar(item):
    p = item["produto"]
    fontes = ", ".join(item.get("fontes", []))
    texto = (
        f"🔥 <b>{html.escape(item['termo'][:80])}</b>\n\n"
        f"📦 {html.escape(p['titulo'][:120])}\n"
        f"💰 <b>{preco_br(p['preco'])}</b>\n"
        + ("🚚 Frete grátis\n" if p.get("frete_gratis") else "")
        + f"📌 {html.escape(p.get('categoria', ''))} · {html.escape(fontes)}\n\n"
        f"🔗 <a href=\"{html.escape(p['link'])}\">Ver oferta no Mercado Livre</a>\n\n"
        "<i>Link de afiliado. Preços e estoque podem variar.</i>"
    )
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado")
        return False

    data = {"chat_id": TG_CHAT, "caption": texto, "parse_mode": "HTML"}
    if p.get("imagem"):
        data["photo"] = p["imagem"]
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", data=data, timeout=25)
    else:
        data["text"] = texto
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=25)

    print("Telegram:", r.status_code, r.text[:150])
    return r.ok

def main():
    agora = dt.datetime.now(TZ)
    print(f"Iniciando radar em {agora.isoformat()}")
    h = headers_ml()
    ranking = montar_ranking(h)
    if not ranking:
        print("Nenhum produto coletado.")
        return

    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": ranking[:15]},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Salvos {len(ranking)} itens")

    if not (TG_TOKEN and TG_CHAT):
        print("Sem TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID")
        return
    if not (8 <= agora.hour < 23):
        print("Fora do horário de postagem (8h–23h)")
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