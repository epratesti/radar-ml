"""Radar ML + Shopee → Telegram + Facebook Page.

ML: search (com fallback para catálogo /products/search) + highlights.
Shopee: Open API afiliado GraphQL productOfferV2.
Horário SP: 07h–22h.

v3.4 (marketing + engagement):
- Seleção por pontuação (desconto, vendas, frete, comissão) em vez de sorteio puro.
- Texto novo: 1ª linha já com gancho + produto + preço (o feed corta o resto),
  desconto, vendidos, frete, hashtags e pergunta no Facebook. Vários modelos de gancho.
- Título limpo (sem CAIXA ALTA, sem palavras de spam, cortado no limite de palavra).
- Imagem "card" gerada com Pillow (produto centralizado, selo de desconto, preço grande).
- Telegram com botão inline e fallback para texto se a foto falhar.
- Posts espaçados (pausa aleatória) em vez de rajada.
- NOVO: além do link na legenda, tenta publicar o link também no 1º comentário
  do Facebook (aumenta engajamento). Se o comentário falhar, o post continua
  normal com o link na legenda (zero risco). Controlado por FB_LINK_NO_COMENTARIO (padrão=1).
- Menção mais forte de entrega rápida / frete grátis + destaque de desconto alto.
- Se o token do ML falhar, a Shopee continua rodando.
Tudo continua automático (sem interação): só variáveis de ambiente opcionais.
"""

import os, io, json, html, re, random, hashlib, time, unicodedata, datetime as dt
from itertools import zip_longest
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    PIL_OK = True
except Exception:
    PIL_OK = False


def env_int(nome, padrao):
    try:
        return int((os.getenv(nome) or "").strip() or padrao)
    except ValueError:
        return padrao


AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
AFF_PARAMS = os.getenv("ML_AFFILIATE_PARAMS", "matt_tool={id}")

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()

POSTS_POR_GRUPO = env_int("POSTS_POR_GRUPO", 2)
MAX_POSTS = env_int("MAX_POSTS", 8)
REPETIR_APOS_DIAS = env_int("REPETIR_APOS_DIAS", 3)

SHOPEE_POSTS = env_int("SHOPEE_POSTS", 3)
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()
SHOPEE_GQL = "https://open-api.affiliate.shopee.com.br/graphql"

POOL_FATOR = max(1, env_int("POOL_FATOR", 3))
DESCONTO_MIN = env_int("DESCONTO_MIN", 0)
PAUSA_MIN = max(0, env_int("PAUSA_MIN_SEG", 45))
PAUSA_MAX = max(PAUSA_MIN, env_int("PAUSA_MAX_SEG", 120))
IMG_CARD = (os.getenv("IMG_CARD", "1").strip() != "0")
BRAND_NAME = os.getenv("BRAND_NAME", "").strip()
FB_LINK_NO_COMENTARIO = (os.getenv("FB_LINK_NO_COMENTARIO", "1").strip() != "0")

API = "https://api.mercadolibre.com"
FB_API = "https://graph.facebook.com/v21.0"

ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

FB_ATIVO = bool(FB_PAGE_ID and FB_ACCESS_TOKEN)
FB_FALHAS = 0
FB_ERROS_FATAIS = {3, 10, 102, 190, 200, 240, 283, 368}

SEARCH_403 = 0
SEARCH_OFF = False
CAT_FALHAS = 0
CAT_OFF = False

GRUPOS = [
    {"nome": "Eletrônicos", "cats": ["MLB1000", "MLB1051", "MLB1648"],
     "queries": ["fone bluetooth", "carregador turbo", "smartwatch", "tv led", "ssd 1tb", "power bank", "mouse gamer", "caixa de som"]},
    {"nome": "Casa e Eletro", "cats": ["MLB1574", "MLB5726"],
     "queries": ["air fryer", "liquidificador", "aspirador", "panela pressão", "sofá", "organizador", "cafeteira", "ventilador"]},
    {"nome": "Moda", "cats": ["MLB1430", "MLB3937"],
     "queries": ["tênis", "mochila", "relógio", "camiseta", "calça jeans", "óculos sol", "chinelo", "bolsa"]},
    {"nome": "Esportes e Beleza", "cats": ["MLB1276", "MLB1246", "MLB1132"],
     "queries": ["whey protein", "creatina", "perfume", "shampoo", "kit skincare", "bola futebol", "garrafa térmica"]},
]

SHOPEE_QUERIES = [
    "fone bluetooth", "smartwatch", "carregador turbo", "power bank",
    "air fryer", "liquidificador", "aspirador", "cafeteira",
    "tenis", "mochila", "relogio", "chinelo",
    "whey protein", "perfume", "kit skincare", "garrafa termica",
]

CAT_NOMES = {
    "MLB1000": "Eletrônicos", "MLB1051": "Celulares", "MLB1648": "Informática",
    "MLB1574": "Casa", "MLB5726": "Eletrodomésticos", "MLB1430": "Moda",
    "MLB3937": "Relógios", "MLB1276": "Esportes", "MLB1246": "Beleza", "MLB1132": "Brinquedos",
}

HASHTAGS = {
    "Eletrônicos": ["#Eletronicos", "#Tecnologia"],
    "Casa e Eletro": ["#Casa", "#Eletrodomesticos"],
    "Moda": ["#Moda"],
    "Esportes e Beleza": ["#Fitness", "#Beleza"],
    "Shopee": ["#Shopee"],
}

GANCHOS_DESC = ["🔥 {d}% OFF", "💥 PROMOÇÃO -{d}%", "🏷️ DESCONTO DE {d}%"]
GANCHOS_NORMAL = ["🛒 Achado", "👀 Olha esse preço", "⭐ Boa oferta"]
PERGUNTAS_FB = [
    "Você levaria? 👇",
    "Já usa ou conhece? Conta aqui 👇",
    "Marca quem precisa disso 👇",
    "Vale a pena? Comenta 👇",
]

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/3.4", "Accept": "application/json"})


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def calc_desconto(preco, original):
    try:
        original = float(original or 0)
    except (TypeError, ValueError):
        return 0, 0.0
    if original > preco > 0:
        d = int(round((1 - preco / original) * 100))
        if 3 <= d <= 90:
            return d, original
    return 0, 0.0


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_vendidos(n):
    if n >= 1000:
        k = n / 1000
        return f"+{int(k)} mil" if k >= 10 else f"+{k:.1f} mil".replace(".", ",")
    return f"+{(n // 10) * 10}"


RUIDO_TITULO = re.compile(
    r"\b(frete gr[aá]tis|envio imediato|envio r[aá]pido|pronta entrega|promo[cç][aã]o|oferta)\b", re.I)
EMOJIS = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def limpar_titulo(t, max_len=60):
    t = html.unescape(t or "")
    t = EMOJIS.sub(" ", t)
    t = RUIDO_TITULO.sub(" ", t)
    t = re.sub(r"[|•·]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–,;:/")
    letras = [c for c in t if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.6:
        t = " ".join(w.capitalize() for w in t.split())
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].rstrip(" -–,;:/(") + "…"
    return t or "Produto em oferta"


def slug_tag(txt):
    s = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]", "", s)
    return "".join(w.capitalize() for w in s.split())


def hashtags(p, n):
    tags = []
    termo = slug_tag(p.get("termo") or "")
    if termo:
        tags.append("#" + termo)
    tags += HASHTAGS.get(p.get("categoria"), [])
    tags.append("#Oferta")
    out = []
    for t in tags:
        if t not in out:
            out.append(t)
    return " ".join(out[:n])


def norm(t):
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9áéíóúâêôãõç\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:80]


def headers_ml():
    cid, sec = os.getenv("ML_CLIENT_ID"), os.getenv("ML_CLIENT_SECRET")
    if not (cid and sec):
        print("AVISO: secrets ML ausentes — seguindo só com Shopee")
        return {}
    try:
        r = S.post(f"{API}/oauth/token", data={
            "grant_type": "client_credentials", "client_id": cid, "client_secret": sec
        }, timeout=20)
        r.raise_for_status()
        print("Token ML OK")
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    except Exception as e:
        print("AVISO: token ML falhou — seguindo só com Shopee:", e)
        return {}


def link_afiliado(url):
    sep = "&" if "?" in url else "?"
    return url + sep + AFF_PARAMS.format(id=AFF_ID)


def carregar_estado():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(STATE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            return {"ids": {}, "titulos": {}}
        if data and "ids" not in data:
            return {"ids": data, "titulos": {}}
        data.setdefault("ids", {})
        data.setdefault("titulos", {})
        return data
    except Exception:
        return {"ids": {}, "titulos": {}}


def limpar_estado(estado, agora):
    limite = agora - dt.timedelta(days=REPETIR_APOS_DIAS)
    ids, tits = {}, {}
    for k, v in estado.get("ids", {}).items():
        try:
            if dt.datetime.fromisoformat(v) > limite:
                ids[k] = v
        except Exception:
            pass
    for k, v in estado.get("titulos", {}).items():
        try:
            if dt.datetime.fromisoformat(v) > limite:
                tits[k] = v
        except Exception:
            pass
    return {"ids": ids, "titulos": tits}


def ja_postado(estado, pid, titulo, item_id=None):
    if str(pid) in estado["ids"]:
        return True
    if item_id and str(item_id) in estado["ids"]:
        return True
    nt = norm(titulo)
    return bool(nt and nt in estado["titulos"])


def registrar(estado, pid, titulo, agora, item_id=None):
    ts = agora.isoformat()
    estado["ids"][str(pid)] = ts
    if item_id:
        estado["ids"][str(item_id)] = ts
    nt = norm(titulo)
    if nt:
        estado["titulos"][nt] = ts


def produto_search(item, label):
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
    pid = str(item.get("catalog_product_id") or mid)
    mid_fmt = (mid[:3] + "-" + mid[3:]) if mid.startswith("MLB") and "-" not in mid else mid
    desc, orig = calc_desconto(preco, item.get("original_price"))
    return {
        "id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
        "frete_gratis": frete, "categoria": label, "fonte": "ml",
        "link": link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}"),
        "item_id": mid,
        "desconto": desc, "preco_original": orig,
        "vendidos": to_int(item.get("sold_quantity")),
    }


def ml_get(url, h, params=None):
    r = S.get(url, headers=h or {}, params=params, timeout=25)
    if r.status_code == 403 and h:
        r = S.get(url, headers={"Accept": "application/json", "User-Agent": "radar-ml/3.4"},
                  params=params, timeout=25)
    return r


def desconto_item(item):
    try:
        preco = float(item.get("price") or 0)
    except (TypeError, ValueError):
        return 0
    return calc_desconto(preco, item.get("original_price"))[0]


def buscar(query, h, vistos, vistos_t, label, n):
    global SEARCH_403, SEARCH_OFF
    achados = []
    if SEARCH_OFF:
        return achados
    try:
        offset = random.choice([0, 0, 10, 20, 30, 40])
        r = ml_get(f"{API}/sites/MLB/search", h, params={
            "q": query, "limit": 50, "offset": offset,
            "sort": random.choice(["relevance", "sold_quantity_desc"]),
        })
        if r.status_code != 200:
            print(f"  search {query}: {r.status_code}")
            if r.status_code == 403:
                SEARCH_403 += 1
                if SEARCH_403 >= 3:
                    SEARCH_OFF = True
                    print("  → /sites/MLB/search bloqueado (403). Usando catálogo + highlights nesta execução.")
            return achados
        SEARCH_403 = 0
        results = list(r.json().get("results") or [])
        results.sort(key=lambda it: desconto_item(it) + random.uniform(0, 12), reverse=True)
        for item in results:
            if len(achados) >= n:
                break
            pid = str(item.get("catalog_product_id") or item.get("id") or "")
            titulo = item.get("title") or ""
            nt = norm(titulo)
            if not pid or pid in vistos or (nt and nt in vistos_t):
                continue
            p = produto_search(item, label)
            if not p:
                continue
            p["termo"] = query
            vistos.add(p["id"])
            if p.get("item_id"):
                vistos.add(str(p["item_id"]))
            if nt:
                vistos_t.add(nt)
            achados.append(p)
            print(f"  OK [ML/{label}] R$ {p['preco']:.2f} (-{p['desconto']}%) — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro {query}:", e)
    return achados


def produto_catalogo(pid, h, label, vistos, vistos_t):
    pid = str(pid or "")
    if not pid or pid in vistos:
        return None
    titulo, preco, foto, frete, mid = "", 0.0, "", False, None
    original, vendidos = 0, 0
    try:
        rp = ml_get(f"{API}/products/{pid}", h)
        if rp.status_code == 200:
            d = rp.json()
            titulo = d.get("name") or d.get("title") or ""
            pics = d.get("pictures") or []
            if pics:
                foto = (pics[0].get("url") or pics[0].get("secure_url") or "").replace("http://", "https://")
        ri = ml_get(f"{API}/products/{pid}/items", h)
        if ri.status_code == 200:
            results = ri.json().get("results") or []
            if results:
                it = min(results, key=lambda x: float(x.get("price") or 1e18))
                preco = float(it.get("price") or 0)
                frete = (it.get("shipping") or {}).get("free_shipping", False)
                mid = it.get("id") or it.get("item_id")
                original = it.get("original_price")
                vendidos = to_int(it.get("sold_quantity"))
    except Exception as e:
        print(f"  product {pid}:", e)
        return None
    if not titulo or preco <= 0:
        return None
    nt = norm(titulo)
    if nt and nt in vistos_t:
        return None
    if mid:
        m = str(mid)
        mid_fmt = (m[:3] + "-" + m[3:]) if m.startswith("MLB") and "-" not in m else m
        link = link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}")
    else:
        link = link_afiliado(f"https://www.mercadolivre.com.br/p/{pid}")
    vistos.add(pid)
    if mid:
        vistos.add(str(mid))
    if nt:
        vistos_t.add(nt)
    desc, orig = calc_desconto(preco, original)
    return {"id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
            "frete_gratis": frete, "categoria": label, "fonte": "ml",
            "link": link, "item_id": mid,
            "desconto": desc, "preco_original": orig, "vendidos": vendidos}


def buscar_catalogo(query, h, vistos, vistos_t, label, n):
    global CAT_FALHAS, CAT_OFF
    achados = []
    if CAT_OFF:
        return achados
    try:
        r = ml_get(f"{API}/products/search", h, params={
            "status": "active", "site_id": "MLB", "q": query, "limit": 20,
        })
        if r.status_code != 200:
            print(f"  catálogo {query}: {r.status_code}")
            CAT_FALHAS += 1
            if CAT_FALHAS >= 3:
                CAT_OFF = True
                print("  → /products/search indisponível. Usando só highlights nesta execução.")
            return achados
        CAT_FALHAS = 0
        results = list(r.json().get("results") or [])
        random.shuffle(results)
        for item in results[:8]:
            if len(achados) >= n:
                break
            p = produto_catalogo(item.get("id"), h, label, vistos, vistos_t)
            if not p:
                continue
            p["termo"] = query
            achados.append(p)
            print(f"  OK [ML/{label}/cat] R$ {p['preco']:.2f} (-{p['desconto']}%) — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro catálogo {query}:", e)
    return achados


def buscar_highlights(cat_id, h, vistos, vistos_t, n):
    nome = CAT_NOMES.get(cat_id, cat_id)
    achados = []
    try:
        r = ml_get(f"{API}/highlights/MLB/category/{cat_id}", h)
        if r.status_code != 200:
            print(f"  highlights {nome}: {r.status_code}")
            return achados
        items = list(r.json().get("content", [])[:30])
        random.shuffle(items)
        for item in items:
            if len(achados) >= n:
                break
            p = produto_catalogo(item.get("id"), h, nome, vistos, vistos_t)
            if not p:
                continue
            achados.append(p)
            print(f"  OK [ML/{nome}/hl] R$ {p['preco']:.2f} (-{p['desconto']}%) — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro highlights {nome}:", e)
    return achados


def montar_ml(h, estado):
    vistos = set(str(k) for k in estado["ids"])
    vistos_t = set(estado["titulos"])
    out = []
    max_ml = max(1, MAX_POSTS - SHOPEE_POSTS) * POOL_FATOR
    grupos = list(GRUPOS)
    random.shuffle(grupos)
    for g in grupos:
        if len(out) >= max_ml:
            break
        qs = list(g["queries"])
        random.shuffle(qs)
        got = 0
        print(f"Grupo ML {g['nome']}: até {POSTS_POR_GRUPO * POOL_FATOR}")
        for q in qs:
            if got >= POSTS_POR_GRUPO * POOL_FATOR or len(out) >= max_ml:
                break
            falta = min(POSTS_POR_GRUPO * POOL_FATOR - got, max_ml - len(out))
            novos = buscar(q, h, vistos, vistos_t, g["nome"], falta)
            if not novos and not CAT_OFF:
                novos = buscar_catalogo(q, h, vistos, vistos_t, g["nome"], falta)
            out.extend(novos)
            got += len(novos)
        if got < POSTS_POR_GRUPO * POOL_FATOR:
            for cat in g["cats"]:
                if got >= POSTS_POR_GRUPO * POOL_FATOR or len(out) >= max_ml:
                    break
                falta = min(POSTS_POR_GRUPO * POOL_FATOR - got, max_ml - len(out))
                novos = buscar_highlights(cat, h, vistos, vistos_t, falta)
                out.extend(novos)
                got += len(novos)
    return out


def shopee_request(query_str: str):
    if not (SHOPEE_APP_ID and SHOPEE_SECRET):
        return None
    payload_obj = {"query": query_str}
    payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()))
    factor = f"{SHOPEE_APP_ID}{ts}{payload}{SHOPEE_SECRET}"
    sig = hashlib.sha256(factor.encode("utf-8")).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={ts}, Signature={sig}",
    }
    r = S.post(SHOPEE_GQL, data=payload.encode("utf-8"), headers=headers, timeout=30)
    return r


def buscar_shopee(keyword, vistos, vistos_t, n):
    achados = []
    if not (SHOPEE_APP_ID and SHOPEE_SECRET):
        return achados
    kw = json.dumps(keyword, ensure_ascii=False)
    q = (
        "{"
        f"productOfferV2(keyword: {kw}, listType: 0, sortType: 2, page: 1, limit: 20) {{"
        "nodes { itemId productName offerLink imageUrl priceMin priceMax "
        "priceDiscountRate sales commissionRate shopName }"
        " pageInfo { page limit hasNextPage }"
        "}}"
    )
    try:
        r = shopee_request(q)
        if r is None:
            return achados
        if r.status_code != 200:
            print(f"  Shopee '{keyword}': HTTP {r.status_code} {r.text[:180]}")
            return achados
        data = r.json()
        if data.get("errors"):
            print(f"  Shopee errors: {str(data['errors'])[:200]}")
            return achados
        nodes = ((data.get("data") or {}).get("productOfferV2") or {}).get("nodes") or []
        random.shuffle(nodes)
        for node in nodes:
            if len(achados) >= n:
                break
            titulo = node.get("productName") or ""
            link = node.get("offerLink") or node.get("productLink") or ""
            pid = f"SHOPEE-{node.get('itemId')}"
            try:
                preco = float(node.get("priceMin") or node.get("priceMax") or 0)
            except (TypeError, ValueError):
                preco = 0
            if not titulo or not link or preco <= 0:
                continue
            nt = norm(titulo)
            if pid in vistos or (nt and nt in vistos_t):
                continue
            foto = node.get("imageUrl") or ""
            desc = to_int(node.get("priceDiscountRate"))
            p = {
                "id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
                "frete_gratis": False, "categoria": "Shopee", "fonte": "shopee",
                "link": link, "item_id": str(node.get("itemId") or ""),
                "comissao": node.get("commissionRate"), "loja": node.get("shopName") or "",
                "desconto": desc, "preco_original": 0, "vendidos": to_int(node.get("sales")),
                "termo": keyword,
            }
            vistos.add(pid)
            if p["item_id"]:
                vistos.add(p["item_id"])
            if nt:
                vistos_t.add(nt)
            achados.append(p)
            print(f"  OK [Shopee] R$ {preco:.2f} (-{desc}%) — {titulo[:50]}")
    except Exception as e:
        print(
