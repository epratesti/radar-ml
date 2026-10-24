"""Radar: produtos diversificados do Mercado Livre → Telegram.
Evita repetição entre execuções (bloqueio por ID, item_id e título normalizado).
Também bloqueia itens já presentes em docs/trends.json.
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
POSTS_POR_GRUPO = int(os.getenv("POSTS_POR_GRUPO", "2"))
MAX_POSTS = int(os.getenv("MAX_POSTS", "12"))
# Dias sem repetir o mesmo produto (ID ou título similar). Padrão: 7
REPETIR_APOS_DIAS = int(os.getenv("REPETIR_APOS_DIAS", "7"))
API = "https://api.mercadolibre.com"
ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

GRUPOS = [
    {
        "nome": "Eletrônicos e Informática",
        "cats": ["MLB1000", "MLB1051", "MLB1648", "MLB1144"],
        "queries": [
            "tv led smart", "fone bluetooth", "carregador turbo", "smartwatch",
            "caixa de som bluetooth", "mouse gamer", "teclado sem fio",
            "webcam full hd", "ssd 1tb", "pendrive 128gb", "tablet android",
            "power bank", "cabo usb c", "suporte notebook", "notebook",
            "monitor gamer", "impressora", "roteador wifi", "hd externo",
            "controle xbox", "jogo ps5", "echo dot", "chromecast",
        ],
    },
    {
        "nome": "Casa, Móveis e Eletrodomésticos",
        "cats": ["MLB1574", "MLB5726"],
        "queries": [
            "armário", "guarda roupa", "sofá", "mesa de jantar", "cama box",
            "estante", "rack tv", "air fryer", "liquidificador",
            "aspirador de pó", "ventilador de teto", "ferro de passar",
            "panela de pressão", "jogo de lençol", "organizador closet",
            "cortina", "tapete sala", "luminária de mesa", "geladeira",
            "máquina de lavar", "microondas", "cafeteira", "sanduicheira",
            "frigideira antiaderente", "jogo de panelas", "organizador cozinha",
        ],
    },
    {
        "nome": "Moda, Calçados e Acessórios",
        "cats": ["MLB1430", "MLB3937", "MLB1276"],
        "queries": [
            "tênis", "sapato social", "sandália", "chinelo",
            "calça jeans", "calça legging", "camisa", "camiseta",
            "mochila", "bolsa", "carteira", "cinto",
            "relógio", "óculos de sol", "jaqueta", "bermuda",
            "vestido", "blusa feminina", "tênis corrida", "boné",
            "meia esportiva", "cinto masculino", "bolsa feminina",
        ],
    },
    {
        "nome": "Esportes, Beleza e Saúde",
        "cats": ["MLB1276", "MLB1246", "MLB264586"],
        "queries": [
            "whey protein", "creatina", "colchonete academia", "halter",
            "perfume", "kit skincare", "shampoo", "escova de cabelo",
            "bicicleta", "patins", "bola futebol", "garrafa térmica",
            "creme hidratante", "protetor solar", "secador de cabelo",
            "barra de proteína", "corda de pular", "yoga mat",
        ],
    },
    {
        "nome": "Bebês, Brinquedos e Papelaria",
        "cats": ["MLB1384", "MLB1132", "MLB1196"],
        "queries": [
            "fralda descartável", "carrinho de bebê", "cadeira de alimentação",
            "brinquedo educativo", "lego", "boneca", "carrinho controle remoto",
            "caderno universitário", "caneta esferográfica", "mochila escolar",
            "kit material escolar", "quebra cabeça", "pelúcia",
        ],
    },
    {
        "nome": "Ferramentas, Automotivo e Pet",
        "cats": ["MLB407134", "MLB1743", "MLB1071"],
        "queries": [
            "furadeira", "kit ferramentas", "chave de fenda",
            "capa de banco carro", "óleo motor", "pneu",
            "ração cachorro", "ração gato", "arranhador gato",
            "coleira", "cama pet", "brinquedo cachorro",
            "lanterna led", "extensão elétrica", "tomada inteligente",
        ],
    },
]

CAT_NOMES = {
    "MLB1051": "Celulares", "MLB1430": "Moda", "MLB1144": "Games",
    "MLB1000": "Eletrônicos", "MLB1276": "Esportes", "MLB1648": "Informática",
    "MLB1246": "Beleza", "MLB5726": "Eletrodomésticos", "MLB1132": "Brinquedos",
    "MLB1574": "Casa", "MLB3937": "Relógios", "MLB1384": "Bebês",
    "MLB1196": "Papelaria", "MLB407134": "Ferramentas", "MLB1743": "Automotivo",
    "MLB1071": "Pet Shop", "MLB264586": "Saúde",
}

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/2.1", "Accept": "application/json"})


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


def chave_titulo_curta(t: str) -> str:
    """Primeiras palavras do título para pegar variações do mesmo produto."""
    nt = normalizar_titulo(t)
    palavras = nt.split()
    return " ".join(palavras[:6]) if palavras else ""


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


def incorporar_trends(estado, agora):
    """Inclui produtos do trends.json no bloqueio (evita repetir o lote anterior)."""
    if REPETIR_APOS_DIAS <= 0:
        return estado
    try:
        if not TRENDS.exists():
            return estado
        data = json.loads(TRENDS.read_text(encoding="utf-8") or "{}")
        itens = data.get("itens") or []
        ts = data.get("atualizado") or agora.isoformat()
        for it in itens:
            pid = str(it.get("id") or "")
            titulo = it.get("titulo") or ""
            if pid and pid not in estado["ids"]:
                estado["ids"][pid] = ts
            nt = normalizar_titulo(titulo)
            if nt and nt not in estado["titulos"]:
                estado["titulos"][nt] = ts
            ct = chave_titulo_curta(titulo)
            if ct and ct not in estado["titulos"]:
                estado["titulos"][ct] = ts
        print(f"Trends incorporados: +{len(itens)} itens no bloqueio")
    except Exception as e:
        print("Aviso ao ler trends:", e)
    return estado


def limpar_estado(estado, agora):
    """Remove entradas antigas além de REPETIR_APOS_DIAS."""
    if REPETIR_APOS_DIAS <= 0:
        return {"ids": {}, "titulos": {}}
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


def ja_postado(estado, pid, titulo, item_id=None):
    """Verifica se produto (ou título muito parecido) já foi postado recentemente."""
    if REPETIR_APOS_DIAS <= 0:
        return False
    ids = estado.get("ids", {})
    if str(pid) in ids:
        return True
    if item_id and str(item_id) in ids:
        return True
    titulos = estado.get("titulos", {})
    nt = normalizar_titulo(titulo)
    if nt and nt in titulos:
        return True
    ct = chave_titulo_curta(titulo)
    if ct and ct in titulos:
        return True
    # Checagem extra: título curto já bloqueado como prefixo
    if ct:
        for t in titulos:
            if t.startswith(ct) or ct.startswith(t[: max(10, len(ct) - 5)]):
                return True
    return False


def registrar_postado(estado, pid, titulo, agora, item_id=None):
    """Registra produto postado para evitar repetição."""
    if REPETIR_APOS_DIAS <= 0:
        return
    ts = agora.isoformat()
    estado.setdefault("ids", {})[str(pid)] = ts
    if item_id:
        estado["ids"][str(item_id)] = ts
    nt = normalizar_titulo(titulo)
    if nt:
        estado.setdefault("titulos", {})[nt] = ts
    ct = chave_titulo_curta(titulo)
    if ct:
        estado.setdefault("titulos", {})[ct] = ts


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
        "id": str(pid), "titulo": titulo, "preco": preco, "imagem": foto,
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


def buscar_varios_query(query, cat_id, h, vistos_ids, vistos_titulos, label, quantos):
    achados = []
    try:
        offset = random.choice([0, 10, 20, 30, 40, 50, 60, 80])
        params = {
            "q": query, "limit": 50, "offset": offset,
            "sort": random.choice(["relevance", "price_asc", "sold_quantity_desc"]),
        }
        if cat_id:
            params["category"] = cat_id
        r = S.get(f"{API}/sites/MLB/search", headers=h, params=params, timeout=25)
        if r.status_code != 200:
            return achados
        results = r.json().get("results") or []
        random.shuffle(results)
        for item in results:
            if len(achados) >= quantos:
                break
            pid = str(item.get("catalog_product_id") or item.get("id") or "")
            mid = str(item.get("id") or "")
            if not pid or pid in vistos_ids or (mid and mid in vistos_ids):
                continue
            titulo = item.get("title") or ""
            nt = normalizar_titulo(titulo)
            ct = chave_titulo_curta(titulo)
            if (nt and nt in vistos_titulos) or (ct and ct in vistos_titulos):
                continue
            p = None
            if item.get("catalog_product_id"):
                p = montar_produto(item["catalog_product_id"], h, label)
            if not p:
                p = produto_de_search_item(item, label)
            if not p:
                continue
            if p["id"] in vistos_ids:
                continue
            nt2 = normalizar_titulo(p["titulo"])
            ct2 = chave_titulo_curta(p["titulo"])
            if (nt2 and nt2 in vistos_titulos) or (ct2 and ct2 in vistos_titulos):
                continue
            vistos_ids.add(p["id"])
            if p.get("item_id"):
                vistos_ids.add(str(p["item_id"]))
            if nt2:
                vistos_titulos.add(nt2)
            if ct2:
                vistos_titulos.add(ct2)
            achados.append(p)
            print(f"  OK [{label}] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro search '{query}':", e)
    return achados


def buscar_varios_highlights(cat_id, h, vistos_ids, vistos_titulos, quantos):
    nome = CAT_NOMES.get(cat_id, cat_id)
    achados = []
    try:
        r = S.get(f"{API}/highlights/MLB/category/{cat_id}", headers=h, timeout=20)
        if r.status_code != 200:
            return achados
        items = list(r.json().get("content", [])[:40])
        random.shuffle(items)
        for item in items:
            if len(achados) >= quantos:
                break
            pid = str(item.get("id") or "")
            if not pid or pid in vistos_ids:
                continue
            p = montar_produto(pid, h, nome)
            if not p:
                continue
            nt = normalizar_titulo(p["titulo"])
            ct = chave_titulo_curta(p["titulo"])
            if (nt and nt in vistos_titulos) or (ct and ct in vistos_titulos):
                continue
            vistos_ids.add(p["id"])
            if p.get("item_id"):
                vistos_ids.add(str(p["item_id"]))
            if nt:
                vistos_titulos.add(nt)
            if ct:
                vistos_titulos.add(ct)
            achados.append(p)
            print(f"  OK [{nome}/hl] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro highlights {nome}:", e)
    return achados


def varios_do_grupo(grupo, h, vistos_ids, vistos_titulos, quantos):
    achados = []
    queries = list(grupo.get("queries", []))
    random.shuffle(queries)
    for q in queries:
        if len(achados) >= quantos:
            break
        falta = quantos - len(achados)
        cat = random.choice(grupo["cats"]) if grupo["cats"] else None
        novos = buscar_varios_query(q, cat, h, vistos_ids, vistos_titulos, grupo["nome"], falta)
        achados.extend(novos)
    if len(achados) < quantos:
        for cat_id in grupo["cats"]:
            if len(achados) >= quantos:
                break
            falta = quantos - len(achados)
            novos = buscar_varios_highlights(cat_id, h, vistos_ids, vistos_titulos, falta)
            achados.extend(novos)
    return achados


def montar_diversificado(h, estado):
    """Busca produtos de todos os grupos, respeitando histórico de postados."""
    vistos_ids = set(str(k) for k in estado.get("ids", {}).keys())
    vistos_titulos = set(estado.get("titulos", {}).keys())
    escolhidos = []
    grupos = list(GRUPOS)
    random.shuffle(grupos)
    for grupo in grupos:
        if len(escolhidos) >= MAX_POSTS:
            break
        falta_total = MAX_POSTS - len(escolhidos)
        n = min(POSTS_POR_GRUPO, falta_total)
        print(f"Grupo {grupo['nome']}: buscando até {n} (já vistos: {len(vistos_ids)} ids)")
        novos = varios_do_grupo(grupo, h, vistos_ids, vistos_titulos, n)
        escolhidos.extend(novos)
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
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    print(f"Meta: {POSTS_POR_GRUPO} por grupo, máx {MAX_POSTS} total")
    print(f"Bloqueio entre runs: {'DESLIGADO' if REPETIR_APOS_DIAS <= 0 else f'{REPETIR_APOS_DIAS} dias'}")
    h = headers_ml()
    if not h:
        return
    estado = limpar_estado(carregar_estado(), agora)
    estado = incorporar_trends(estado, agora)
    print(f"Bloqueados (histórico): {len(estado.get('ids', {}))} ids, {len(estado.get('titulos', {}))} títulos")
    ranking = montar_diversificado(h, estado)
    # Filtra de novo na hora de montar o ranking final
    ranking = [p for p in ranking if not ja_postado(estado, p["id"], p["titulo"], p.get("item_id"))]
    print("Produtos selecionados (após filtro):", len(ranking))
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    trends_itens = [{k: v for k, v in p.items() if k != "item_id"} for p in ranking]
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": trends_itens},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    if not ranking:
        print("Nada a postar (tudo já bloqueado ou busca vazia)")
        salvar_estado(estado)
        return
    if not (TG_TOKEN and TG_CHAT):
        print("Falta Telegram")
        salvar_estado(estado)
        return
    if not pode_postar_agora(agora):
        print(f"Fora do horário ({agora.hour}h) — produtos salvos em trends, sem postar")
        salvar_estado(estado)
        return
    enviados = 0
    for p in ranking:
        if enviados >= MAX_POSTS:
            break
        if ja_postado(estado, p["id"], p["titulo"], p.get("item_id")):
            print(f"  Pula (já postado): {p['titulo'][:50]}")
            continue
        if postar(p):
            registrar_postado(estado, p["id"], p["titulo"], agora, p.get("item_id"))
            enviados += 1
            salvar_estado(estado)
    salvar_estado(estado)
    print("Postagens enviadas:", enviados)
    print(f"Estado atual: {len(estado.get('ids', {}))} ids bloqueados")


if __name__ == "__main__":
    main()
