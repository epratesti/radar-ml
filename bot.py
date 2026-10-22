"""Radar: 4 produtos diversificados por hora → Telegram.
Evita repetições por 7 dias (ID + título).
Busca ampla no catálogo (não depende só de highlights).
Horário SP: 07h–22h / madrugada 23h, 02h, 05h
"""
import os, json, html, re, random, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
AFF_PARAMS = os.getenv("ML_AFFILIATE_PARAMS", "matt_tool={id}")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
MAX_POSTS = int(os.getenv("MAX_POSTS", "4"))
REPETIR_APOS_DIAS = 7
API = "https://api.mercadolibre.com"
ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

# Pool grande de termos — milhares de anúncios por query no ML
GRUPOS = [
    {
        "nome": "Eletrônicos",
        "cats": ["MLB1000", "MLB1051", "MLB1648"],
        "queries": [
            "tv led smart", "fone bluetooth", "carregador turbo", "smartwatch",
            "caixa de som bluetooth", "mouse gamer", "teclado sem fio",
            "webcam full hd", "ssd 1tb", "pendrive 128gb", "tablet android",
            "power bank", "cabo usb c", "suporte notebook",
        ],
    },
    {
        "nome": "Casa e Móveis",
        "cats": ["MLB1574", "MLB5726"],
        "queries": [
            "armário", "guarda roupa", "sofá", "mesa de jantar", "cama box",
            "estante", "rack tv", "air fryer", "liquidificador",
            "aspirador de pó", "ventilador de teto", "ferro de passar",
            "panela de pressão", "jogo de lençol", "organizador closet",
            "cortina", "tapete sala", "luminária de mesa",
        ],
    },
    {
        "nome": "Moda e Calçados",
        "cats": ["MLB1430", "MLB3937"],
        "queries": [
            "tênis", "sapato social", "sandália", "chinelo",
            "calça jeans", "calça legging", "camisa", "camiseta",
            "mochila", "bolsa", "carteira", "cinto",
            "relógio", "óculos de sol", "jaqueta", "bermuda",
            "vestido", "blusa feminina", "tênis corrida",
        ],
    },
    {
        "nome": "Esportes e Beleza",
        "cats": ["MLB1276", "MLB1246", "MLB1132"],
        "queries": [
            "whey protein", "creatina", "colchonete academia", "halter",
            "perfume", "kit skincare", "shampoo", "escova de cabelo",
            "bicicleta", "patins", "bola futebol", "garrafa térmica",
        ],
    },
]

CAT_NOMES = {
    "MLB1051": "Celulares", "MLB1430": "Moda", "MLB1144": "Games",
    "MLB1000": "Eletrônicos", "MLB1276": "Esportes", "MLB1648": "Informática",
    "MLB1246": "Beleza", "MLB5726": "Eletrodomésticos", "MLB1132": "Brinquedos",
    "MLB1574": "Casa", "MLB3937": "Relógios",
}

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/1.7", "Accept": "application/json"})


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


def normalizar_titulo(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9áéíóúâêôãõç\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def carregar_estado():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = STATE.read_text(encoding="utf-8").strip() or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"ids": {}, "titulos": {}}
        if data and "ids" not in data:
            return {"ids": data, "titulos": {}}
        data.setdefault("ids", {})
        data.setdefault("titulos", {})
        return data
    except Exception as e:
        print("Aviso: estado inválido, reiniciando:", e)
        return {"ids": {}, "titulos": {}}


def limpar_estado(estado, agora):
    limite = agora - dt.timedelta(days=REPETIR_APOS_DIAS)
    ids_limpos, titulos_limpos = {}, {}
    for k, v in estado.get("ids", {}).items():
        try:
            if dt.datetime.fromisoformat(v) > limite:
                ids_limpos[k] = v
        except Exception:
            continue
    for k, v in estado.get("titulos", {}).items():
        try:
            if dt.datetime.fromisoformat(v) > limite:
                titulos_limpos[k] = v
        except Exception:
            continue
    return {"ids": ids_limpos, "titulos": titulos_limpos}


def ja_postado(estado, pid, titulo):
    if pid in estado["ids"]:
        return True
    nt = normalizar_titulo(titulo)
    return bool(nt and nt in estado["titulos"])


def registrar_postado(estado, pid, titulo, agora):
    estado["ids"][pid] = agora.isoformat()
    nt = normalizar_titulo(titulo)
    if nt:
        estado["titulos"][nt] = agora.isoformat()


def escolher_oferta(results):
    if not results:
        return None
    validos = []
    for it in results:
        try:
            preco = float(it.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if preco <= 0:
            continue
        frete = (it.get("shipping") or {}).get("free_shipping", False)
        validos.append({"item": it, "preco": preco, "frete": frete})
    if not validos:
        return None
    precos = sorted(v["preco"] for v in validos)
    mediana = precos[len(precos) // 2]
    piso = mediana * 0.30 if mediana > 0 else 0
    validos = [v for v in validos if v["preco"] >= piso] or validos
    com_frete = [v for v in validos if v["frete"]]
    pool = com_frete if com_frete else validos
    return min(pool, key=lambda x: x["preco"])


def montar_produto(pid, h, categoria):
    titulo, foto = "", ""
    preco, frete = 0.0, False
    item_id = None
    try:
        rp = S.get(f"{API}/products/{pid}", headers=h, timeout=15)
        if rp.status_code == 200:
            d = rp.json()
            titulo = d.get("name") or d.get("title") or ""
            pics = d.get("pictures") or []
            if pics:
                foto = (pics[0].get("url") or pics[0].get("secure_url") or "").replace("http://", "https://")
    except Exception as e:
        print(f"  products/{pid}:", e)
    try:
        ri = S.get(f"{API}/products/{pid}/items", headers=h, timeout=15)
        if ri.status_code == 200:
            escolha = escolher_oferta(ri.json().get("results") or [])
            if escolha:
                preco = escolha["preco"]
                frete = escolha["frete"]
                item_id = escolha["item"].get("id") or escolha["item"].get("item_id")
    except Exception as e:
        print(f"  items/{pid}:", e)
    if not titulo or preco <= 0:
        return None
    if item_id:
        mid = str(item_id)
        mid_fmt = (mid[:3] + "-" + mid[3:]) if mid.startswith("MLB") and "-" not in mid else mid
        link = link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}")
    else:
        link = link_afiliado(f"https://www.mercadolivre.com.br/p/{pid}")
    return {
        "id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
        "frete_gratis": frete, "categoria": categoria, "link": link, "item_id": item_id,
    }


def produto_de_search_item(item, categoria_label):
    titulo = item.get("title") or ""
    try:
        preco = float(item.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if not titulo or preco <= 0:
        return None
    frete = (item.get("shipping") or {}).get("free_shipping", False)
    foto = (item.get("thumbnail") or "").replace("http://", "https://")
    mid = str(item.get("id", ""))
    pid = item.get("catalog_product_id") or mid
    mid_fmt = (mid[:3] + "-" + mid[3:]) if mid.startswith("MLB") and "-" not in mid else mid
    return {
        "id": str(pid), "titulo": titulo, "preco": preco, "imagem": foto,
        "frete_gratis": frete, "categoria": categoria_label,
        "link": link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}"),
        "item_id": mid,
    }


def buscar_query(query, cat_id, h, vistos_ids, vistos_titulos, label):
    """Busca no catálogo amplo (até 50 resultados + offset aleatório)."""
    try:
        offset = random.choice([0, 0, 10, 20, 30, 40, 50])  # varia página
        params = {
            "q": query,
            "limit": 50,
            "offset": offset,
            "sort": random.choice(["relevance", "price_asc", "sold_quantity_desc"]),
        }
        if cat_id:
            params["category"] = cat_id
        r = S.get(f"{API}/sites/MLB/search", headers=h, params=params, timeout=25)
        if r.status_code != 200:
            print(f"Search '{query}': {r.status_code}")
            return None
        results = r.json().get("results") or []
        random.shuffle(results)
        for item in results:
            pid = item.get("catalog_product_id") or item.get("id")
            if not pid or str(pid) in vistos_ids:
                continue
            nt = normalizar_titulo(item.get("title") or "")
            if nt and nt in vistos_titulos:
                continue
            if item.get("catalog_product_id"):
                p = montar_produto(item["catalog_product_id"], h, label)
                if p:
                    print(f"  OK [{label}] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
                    return p
            p = produto_de_search_item(item, label)
            if p:
                print(f"  OK [{label}] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
                return p
    except Exception as e:
        print(f"Erro search '{query}':", e)
    return None


def buscar_highlights(cat_id, h, vistos_ids, vistos_titulos):
    nome = CAT_NOMES.get(cat_id, cat_id)
    try:
        r = S.get(f"{API}/highlights/MLB/category/{cat_id}", headers=h, timeout=20)
        if r.status_code != 200:
            return None
        items = list(r.json().get("content", [])[:30])
        random.shuffle(items)
        for item in items:
            pid = item.get("id")
            if not pid or pid in vistos_ids:
                continue
            p = montar_produto(pid, h, nome)
            if not p:
                continue
            nt = normalizar_titulo(p["titulo"])
            if nt and nt in vistos_titulos:
                continue
            print(f"  OK [{nome}/hl] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
            return p
    except Exception as e:
        print(f"Erro highlights {nome}:", e)
    return None


def um_do_grupo(grupo, h, vistos_ids, vistos_titulos):
    # 1) busca textual (pool enorme) — prioridade
    queries = list(grupo.get("queries", []))
    random.shuffle(queries)
    for q in queries[:6]:  # tenta até 6 termos por grupo
        cat = random.choice(grupo["cats"]) if grupo["cats"] else None
        p = buscar_query(q, cat, h, vistos_ids, vistos_titulos, grupo["nome"])
        if p:
            return p
    # 2) highlights como complemento
    for cat_id in grupo["cats"]:
        p = buscar_highlights(cat_id, h, vistos_ids, vistos_titulos)
        if p:
            return p
    return None


def montar_diversificado(h, estado):
    vistos_ids = set(estado["ids"].keys())
    vistos_titulos = set(estado["titulos"].keys())
    escolhidos = []
    grupos = list(GRUPOS)
    random.shuffle(grupos)
    for grupo in grupos:
        p = um_do_grupo(grupo, h, vistos_ids, vistos_titulos)
        if p:
            vistos_ids.add(p["id"])
            nt = normalizar_titulo(p["titulo"])
            if nt:
                vistos_titulos.add(nt)
            escolhidos.append(p)
        if len(escolhidos) >= MAX_POSTS:
            break
    return escolhidos


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def postar(p):
    cat = html.escape(p.get("categoria", ""))
    titulo = html.escape(p["titulo"][:120])
    preco = preco_br(p["preco"])
    link = html.escape(p["link"])
    frete_linha = "🚚 <b>Frete grátis</b>\n" if p.get("frete_gratis") else ""
    preco_label = f"a partir de <b>{preco}</b>" if p.get("item_id") else f"<b>{preco}</b>"
    texto = (
        f"🔥 <b>Achado do momento — {cat}</b>\n\n"
        f"📦 {titulo}\n\n"
        f"💰 {preco_label}\n"
        f"{frete_linha}"
        f"\n👉 <a href=\"{link}\">Ver oferta no Mercado Livre</a>\n\n"
        f"<i>Link de afiliado · Preços podem variar</i>"
    )
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado")
        return False
    data = {"chat_id": TG_CHAT, "parse_mode": "HTML"}
    if p.get("imagem"):
        data["photo"] = p["imagem"]
        data["caption"] = texto
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", data=data, timeout=30)
    else:
        data["text"] = texto
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=30)
    print("Telegram:", r.status_code, r.text[:150])
    return r.ok


def pode_postar_agora(agora: dt.datetime) -> bool:
    h = agora.hour
    return (7 <= h <= 22) or h in (23, 2, 5)


def salvar_estado(estado):
    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    h = headers_ml()
    if not h:
        return
    estado = limpar_estado(carregar_estado(), agora)
    print(f"Bloqueados (últimos {REPETIR_APOS_DIAS} dias):", len(estado["ids"]))
    ranking = montar_diversificado(h, estado)
    print("Produtos selecionados:", len(ranking))
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    trends_itens = [{k: v for k, v in p.items() if k != "item_id"} for p in ranking]
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": trends_itens},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    if not ranking:
        print("Nada a postar")
        salvar_estado(estado)
        return
    if not (TG_TOKEN and TG_CHAT):
        print("Falta Telegram")
        return
    if not pode_postar_agora(agora):
        print(f"Fora do horário ({agora.hour}h)")
        salvar_estado(estado)
        return
    enviados = 0
    for p in ranking:
        if enviados >= MAX_POSTS:
            break
        if ja_postado(estado, p["id"], p["titulo"]):
            continue
        if postar(p):
            registrar_postado(estado, p["id"], p["titulo"], agora)
            enviados += 1
            salvar_estado(estado)
    salvar_estado(estado)
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
