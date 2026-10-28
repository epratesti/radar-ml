"""Radar ML + Shopee → Telegram + Facebook Page.

ML: search (com fallback para catálogo /products/search) + highlights.
Shopee: Open API afiliado GraphQL productOfferV2.
Horário SP: 07h–22h.

v3.3 (marketing):
- Seleção por pontuação (desconto, vendas, frete, comissão) em vez de sorteio puro.
- Texto novo: 1ª linha já com gancho + produto + preço (o feed corta o resto),
  desconto, vendidos, frete, hashtags e pergunta no Facebook. Vários modelos de gancho.
- Título limpo (sem CAIXA ALTA, sem palavras de spam, cortado no limite de palavra).
- Imagem "card" gerada com Pillow (produto centralizado, selo de desconto, preço grande).
  Se Pillow não estiver instalado ou algo falhar, usa a imagem original (como antes).
- Telegram com botão inline e fallback para texto se a foto falhar.
- Posts espaçados (pausa aleatória) em vez de rajada.
- Corrigido o texto "Shopee — Shopee".
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
except Exception:  # Pillow é opcional: sem ele o bot usa a imagem original
    PIL_OK = False


def env_int(nome, padrao):
    """Lê inteiro de variável de ambiente; vazio/inválido → padrão (não quebra o workflow)."""
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

# Novidades v3.3 (todas opcionais)
POOL_FATOR = max(1, env_int("POOL_FATOR", 3))          # candidatos = posts × fator, depois escolhe os melhores
DESCONTO_MIN = env_int("DESCONTO_MIN", 0)               # 0 = sem filtro; ex.: 15 prioriza ofertas com ≥15%
PAUSA_MIN = max(0, env_int("PAUSA_MIN_SEG", 45))        # pausa entre posts (segundos)
PAUSA_MAX = max(PAUSA_MIN, env_int("PAUSA_MAX_SEG", 120))
IMG_CARD = (os.getenv("IMG_CARD", "1").strip() != "0")  # IMG_CARD=0 desliga o card e usa a foto original
BRAND_NAME = os.getenv("BRAND_NAME", "").strip()        # nome da página, pequeno no canto do card (opcional)

API = "https://api.mercadolibre.com"
FB_API = "https://graph.facebook.com/v21.0"

ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

# Estado de execução (circuit breakers)
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

# Ganchos sem exagero de urgência: só afirmam o que os dados mostram.
GANCHOS_DESC = ["🔥 {d}% OFF", "💥 PROMOÇÃO -{d}%", "🏷️ DESCONTO DE {d}%"]
GANCHOS_NORMAL = ["🛒 Achado", "👀 Olha esse preço", "⭐ Boa oferta"]
PERGUNTAS_FB = [
    "Você levaria? 👇",
    "Já usa ou conhece? Conta aqui 👇",
    "Marca quem precisa disso 👇",
    "Vale a pena? Comenta 👇",
]

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/3.3", "Accept": "application/json"})


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def calc_desconto(preco, original):
    """Retorna (percentual, preco_original) só se o preço original for plausível."""
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
    """Título legível: sem emoji/ruído, sem CAIXA ALTA, cortado no limite de palavra."""
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


# ----------------------------------------------------------------------------
# Mercado Livre
# ----------------------------------------------------------------------------
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
        r = S.get(url, headers={"Accept": "application/json", "User-Agent": "radar-ml/3.3"},
                  params=params, timeout=25)
    return r


def desconto_item(item):
    try:
        preco = float(item.get("price") or 0)
    except (TypeError, ValueError):
        return 0
    return calc_desconto(preco, item.get("original_price"))[0]


def buscar(query, h, vistos, vistos_t, label, n):
    """Busca clássica /sites/MLB/search. Se der 403 várias vezes seguidas, desliga na execução."""
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
        # maiores descontos primeiro (com um pouco de sorteio para variar entre execuções)
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
    """Monta um produto a partir de um ID de catálogo (/products/{id} + /products/{id}/items)."""
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
    """Fallback: /products/search (catálogo) quando /sites/MLB/search está bloqueado."""
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
    """Monta um POOL de candidatos (posts × POOL_FATOR); a escolha final é feita por pontuação."""
    vistos = set(str(k) for k in estado["ids"])
    vistos_t = set(estado["titulos"])
    out = []
    max_ml = max(1, MAX_POSTS - SHOPEE_POSTS) * POOL_FATOR
    por_grupo = POSTS_POR_GRUPO * POOL_FATOR
    grupos = list(GRUPOS)
    random.shuffle(grupos)
    for g in grupos:
        if len(out) >= max_ml:
            break
        qs = list(g["queries"])
        random.shuffle(qs)
        got = 0
        print(f"Grupo ML {g['nome']}: até {por_grupo}")
        for q in qs:
            if got >= por_grupo or len(out) >= max_ml:
                break
            falta = min(por_grupo - got, max_ml - len(out))
            novos = buscar(q, h, vistos, vistos_t, g["nome"], falta)
            if not novos and not CAT_OFF:
                novos = buscar_catalogo(q, h, vistos, vistos_t, g["nome"], falta)
            out.extend(novos)
            got += len(novos)
        if got < por_grupo:
            for cat in g["cats"]:
                if got >= por_grupo or len(out) >= max_ml:
                    break
                falta = min(por_grupo - got, max_ml - len(out))
                novos = buscar_highlights(cat, h, vistos, vistos_t, falta)
                out.extend(novos)
                got += len(novos)
    return out[:max_ml]


# ----------------------------------------------------------------------------
# Shopee
# ----------------------------------------------------------------------------
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


def chave_shopee(node):
    """Prioriza desconto e vendas (com um pouco de sorteio para variar)."""
    try:
        d = float(node.get("priceDiscountRate") or 0)
    except (TypeError, ValueError):
        d = 0.0
    try:
        v = float(node.get("sales") or 0)
    except (TypeError, ValueError):
        v = 0.0
    return d + min(v, 5000) ** 0.5 / 2 + random.uniform(0, 10)


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
        nodes.sort(key=chave_shopee, reverse=True)
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
            if not (3 <= desc <= 90):
                desc = 0
            p = {
                "id": pid,
                "titulo": titulo,
                "preco": preco,
                "imagem": foto,
                "frete_gratis": False,
                "categoria": "Shopee",
                "fonte": "shopee",
                "link": link,
                "item_id": str(node.get("itemId") or ""),
                "comissao": node.get("commissionRate"),
                "loja": node.get("shopName") or "",
                "termo": keyword,
                "desconto": desc,
                "preco_original": 0,   # a API não informa o preço "de"; não inventamos
                "vendidos": to_int(node.get("sales")),
            }
            vistos.add(pid)
            if p["item_id"]:
                vistos.add(p["item_id"])
            if nt:
                vistos_t.add(nt)
            achados.append(p)
            print(f"  OK [Shopee] R$ {preco:.2f} (-{desc}%) — {titulo[:50]}")
    except Exception as e:
        print(f"Erro Shopee '{keyword}':", e)
    return achados


def montar_shopee(estado):
    if not (SHOPEE_APP_ID and SHOPEE_SECRET):
        print("Shopee: secrets ausentes (SHOPEE_APP_ID / SHOPEE_SECRET) — pulando")
        return []
    print("Shopee: buscando ofertas de afiliado...")
    vistos = set(str(k) for k in estado["ids"])
    vistos_t = set(estado["titulos"])
    out = []
    alvo = SHOPEE_POSTS * POOL_FATOR
    qs = list(SHOPEE_QUERIES)
    random.shuffle(qs)
    for q in qs:
        if len(out) >= alvo:
            break
        falta = alvo - len(out)
        novos = buscar_shopee(q, vistos, vistos_t, min(2, falta))
        out.extend(novos)
        time.sleep(0.4)
    return out[:alvo]


# ----------------------------------------------------------------------------
# Seleção (marketing): escolhe as melhores ofertas do pool
# ----------------------------------------------------------------------------
def score(p):
    s = min(p.get("desconto") or 0, 70) * 1.5
    s += min(p.get("vendidos") or 0, 20000) ** 0.5 / 3
    if p.get("frete_gratis"):
        s += 5
    if p.get("imagem"):
        s += 3
    try:
        c = float(p.get("comissao") or 0)
        if c <= 1:
            c *= 100
        s += min(c, 20) * 0.3
    except (TypeError, ValueError):
        pass
    return s


def chave_variedade(p):
    return p.get("categoria") if p.get("fonte") == "ml" else (p.get("termo") or p.get("categoria"))


def escolher(pool, n):
    if not pool or n <= 0:
        return []
    base = pool
    if DESCONTO_MIN > 0:
        fortes = [p for p in pool if (p.get("desconto") or 0) >= DESCONTO_MIN]
        if len(fortes) >= n:
            base = fortes
    ordenados = sorted(base, key=lambda p: score(p) + random.uniform(0, 6), reverse=True)
    escolhidos, cont = [], {}
    for p in ordenados:                       # no máx. 2 por categoria/termo → variedade
        if len(escolhidos) >= n:
            break
        k = chave_variedade(p)
        if cont.get(k, 0) >= 2:
            continue
        cont[k] = cont.get(k, 0) + 1
        escolhidos.append(p)
    for p in ordenados:                       # completa se o limite barrou
        if len(escolhidos) >= n:
            break
        if p not in escolhidos:
            escolhidos.append(p)
    return escolhidos


def selecionar(pool_ml, pool_sh):
    n_sh = min(SHOPEE_POSTS, MAX_POSTS)
    n_ml = max(1, MAX_POSTS - n_sh)
    ml = escolher(pool_ml, n_ml)
    sh = escolher(pool_sh, n_sh)
    inter = [p for par in zip_longest(ml, sh) for p in par if p]   # intercala ML e Shopee
    if len(inter) < MAX_POSTS:                                     # se uma fonte falhou, completa com a outra
        sobra = [p for p in pool_ml + pool_sh if p not in inter]
        sobra.sort(key=score, reverse=True)
        inter += sobra[:MAX_POSTS - len(inter)]
    return inter[:MAX_POSTS]


# ----------------------------------------------------------------------------
# Texto dos posts
# ----------------------------------------------------------------------------
def texto_post(p, para_facebook=False):
    """Facebook: texto puro, 1ª linha com tudo que importa (o feed corta o resto).
    Telegram: HTML, link vai no botão inline."""
    fonte = p.get("fonte") or "ml"
    mkt = "Shopee" if fonte == "shopee" else "Mercado Livre"
    titulo = limpar_titulo(p["titulo"], 50)
    preco = preco_br(p["preco"])
    d = int(p.get("desconto") or 0)
    orig = p.get("preco_original") or 0
    vendidos = int(p.get("vendidos") or 0)
    gancho = random.choice(GANCHOS_DESC).format(d=d) if d >= 5 else random.choice(GANCHOS_NORMAL)

    extras = []
    if vendidos >= 50:
        extras.append(f"✅ {fmt_vendidos(vendidos)} vendidos")
    if p.get("frete_gratis"):
        extras.append("🚚 Frete grátis")
    if p.get("loja"):
        extras.append(f"🏪 {p['loja']}")

    if para_facebook:
        linha_preco = f"💰 De {preco_br(orig)} por {preco}" if orig else f"💰 {preco}"
        partes = [
            f"{gancho} • {titulo} por {preco}",
            "\n".join([linha_preco] + extras),
            f"👉 Ver oferta: {p['link']}",
            random.choice(PERGUNTAS_FB),
            hashtags(p, 3),
            "Link de afiliado · Preço pode mudar",
        ]
        return "\n\n".join(x for x in partes if x)

    esc = html.escape
    linha_preco = f"💰 <s>{preco_br(orig)}</s> <b>{preco}</b>" if orig else f"💰 <b>{preco}</b>"
    corpo = "\n".join([linha_preco] + [esc(x) for x in extras])
    partes = [
        f"<b>{esc(gancho)}</b>\n{esc(titulo)}",
        corpo,
        esc(hashtags(p, 4)),
        f"<i>{mkt} · Link de afiliado · Preço pode mudar</i>",
    ]
    return "\n\n".join(partes)


def rotulo_botao(p):
    return "🛒 Ver na Shopee" if (p.get("fonte") == "shopee") else "🛒 Ver no Mercado Livre"


# ----------------------------------------------------------------------------
# Imagem "card" (Pillow, opcional)
# ----------------------------------------------------------------------------
FONT_CANDIDATAS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
    "Arial Bold.ttf",
]


def fonte(tam):
    for c in FONT_CANDIDATAS:
        try:
            return ImageFont.truetype(c, tam)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:
        return ImageFont.load_default()


def quebrar(draw, texto, fnt, largura, max_linhas):
    linhas, atual = [], ""
    for w in texto.split():
        teste = (atual + " " + w).strip()
        if draw.textlength(teste, font=fnt) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = w
    if atual:
        linhas.append(atual)
    if len(linhas) > max_linhas:
        linhas = linhas[:max_linhas]
        linhas[-1] = linhas[-1].rstrip(" .,;-") + "..."
    return linhas


def baixar_imagem(url):
    candidatos = []
    if re.search(r"-[IVF]\.(jpg|jpeg|webp|png)", url):      # miniatura do ML → versão grande
        candidatos.append(re.sub(r"-[IVF]\.", "-O.", url, count=1))
    candidatos.append(url)
    for c in candidatos:
        try:
            r = S.get(c, headers={"Accept": "image/*,*/*"}, timeout=20)
            if r.status_code == 200 and len(r.content) > 3000:
                return r.content
        except Exception:
            continue
    return None


def gerar_card(p):
    """Quadrado 1080×1080: produto em fundo branco, selo de desconto, preço grande.
    Retorna bytes JPEG ou None (nesse caso usa-se a imagem original)."""
    if not (PIL_OK and IMG_CARD and p.get("imagem")):
        return None
    try:
        conteudo = baixar_imagem(p["imagem"])
        if not conteudo:
            return None
        foto = ImageOps.exif_transpose(Image.open(io.BytesIO(conteudo)))
        rgba = foto.convert("RGBA")
        fundo = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        fundo.alpha_composite(rgba)
        foto = fundo.convert("RGB")

        W = H = 1080
        PAINEL_Y = 760
        card = Image.new("RGB", (W, H), (255, 255, 255))
        area_w, area_h = 940, 680
        esc = min(area_w / foto.width, area_h / foto.height, 2.5)
        novo = foto.resize((max(1, int(foto.width * esc)), max(1, int(foto.height * esc))), Image.LANCZOS)
        card.paste(novo, ((W - novo.width) // 2, 40 + (area_h - novo.height) // 2))

        d = ImageDraw.Draw(card)
        d.rectangle([0, PAINEL_Y, W, H], fill=(17, 24, 39))

        # título (até 2 linhas)
        f_tit = fonte(42)
        y = PAINEL_Y + 28
        for ln in quebrar(d, limpar_titulo(p["titulo"], 80), f_tit, W - 80, 2):
            d.text((40, y), ln, font=f_tit, fill=(255, 255, 255))
            y += 52

        # preço grande (+ preço "de" riscado, só quando é dado real)
        txt_preco = preco_br(p["preco"])
        tam = 92
        f_preco = fonte(tam)
        while d.textlength(txt_preco, font=f_preco) > 560 and tam > 50:
            tam -= 6
            f_preco = fonte(tam)
        py = y + 6
        d.text((40, py), txt_preco, font=f_preco, fill=(250, 204, 21))
        larg = d.textlength(txt_preco, font=f_preco)
        orig = p.get("preco_original") or 0
        if orig:
            f_o = fonte(36)
            t_o = preco_br(orig)
            xo, yo = 40 + larg + 28, py + int(tam * 0.5)
            d.text((xo, yo), t_o, font=f_o, fill=(156, 163, 175))
            d.line([(xo, yo + 20), (xo + d.textlength(t_o, font=f_o), yo + 20)], fill=(156, 163, 175), width=3)

        # chips: frete grátis / vendidos
        chips = []
        if p.get("frete_gratis"):
            chips.append(("FRETE GRÁTIS", (22, 163, 74)))
        v = int(p.get("vendidos") or 0)
        if v >= 50:
            chips.append((f"{fmt_vendidos(v)} VENDIDOS".upper(), (37, 99, 235)))
        cy = py + tam + 24
        x = 40
        f_chip = fonte(28)
        for txt, cor in chips:
            w = int(d.textlength(txt, font=f_chip)) + 36
            d.rounded_rectangle([x, cy, x + w, cy + 48], radius=24, fill=cor)
            d.text((x + 18, cy + 24), txt, font=f_chip, fill=(255, 255, 255), anchor="lm")
            x += w + 14
        if BRAND_NAME:
            d.text((W - 40, cy + 24), BRAND_NAME, font=fonte(28), fill=(156, 163, 175), anchor="rm")

        # selo de desconto
        desc = int(p.get("desconto") or 0)
        if desc >= 5:
            cx, cyb, r = W - 150, 150, 105
            d.ellipse([cx - r, cyb - r, cx + r, cyb + r], fill=(220, 38, 38))
            d.text((cx, cyb - 14), f"-{desc}%", font=fonte(66), fill=(255, 255, 255), anchor="mm")
            d.text((cx, cyb + 46), "OFF", font=fonte(34), fill=(255, 255, 255), anchor="mm")

        buf = io.BytesIO()
        card.save(buf, "JPEG", quality=90, optimize=True)
        print(f"  Card gerado ({len(buf.getvalue()) // 1024} KB)")
        return buf.getvalue()
    except Exception as e:
        print("  Card falhou, usando imagem original:", e)
        return None


# ----------------------------------------------------------------------------
# Canais
# ----------------------------------------------------------------------------
def postar_telegram(p, img=None):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado")
        return False
    texto = texto_post(p, para_facebook=False)
    botao = json.dumps({"inline_keyboard": [[{"text": rotulo_botao(p), "url": p["link"]}]]},
                       ensure_ascii=False)
    base = {"chat_id": TG_CHAT, "parse_mode": "HTML", "reply_markup": botao}
    api = f"https://api.telegram.org/bot{TG_TOKEN}"
    r = None
    try:
        if img:
            r = S.post(f"{api}/sendPhoto", data={**base, "caption": texto},
                       files={"photo": ("oferta.jpg", img, "image/jpeg")}, timeout=60)
        elif p.get("imagem"):
            r = S.post(f"{api}/sendPhoto", data={**base, "photo": p["imagem"], "caption": texto}, timeout=30)
        if r is not None and not r.ok:
            print("Telegram (foto) falhou:", r.status_code, r.text[:120], "→ enviando só texto")
        if r is None or not r.ok:
            r = S.post(f"{api}/sendMessage", data={
                **base, "text": texto,
                "link_preview_options": json.dumps({"is_disabled": True}),
            }, timeout=30)
        print("Telegram:", r.status_code, r.text[:120])
        return r.ok
    except Exception as e:
        print("Erro Telegram:", e)
        return False


def fb_erro(r):
    """Extrai (code, subcode, message) do JSON de erro da Graph API."""
    try:
        e = (r.json() or {}).get("error") or {}
        return e.get("code"), e.get("error_subcode"), e.get("message") or r.text[:180]
    except Exception:
        return None, None, r.text[:180]


def fb_dica(code):
    if code == 240:
        return ("Token sem permissão de postar nessa página. Use o PAGE ACCESS TOKEN da página "
                "(GET /me/accounts), com pages_manage_posts, e confira se FB_PAGE_ID é o ID da página.")
    if code in (190, 102):
        return "Token inválido ou expirado. Gere um novo token de página (de longa duração)."
    if code in (10, 200, 283, 368):
        return ("Falta permissão (pages_manage_posts / pages_read_engagement), o app está em modo "
                "Desenvolvimento ou a conta está restrita.")
    if code == 3:
        return "Funcionalidade indisponível para esse app/token. Revise as permissões do app."
    return "Verifique FB_PAGE_ID, FB_ACCESS_TOKEN e as permissões do app."


def fb_diagnostico():
    """Valida o token antes de postar. Desativa o Facebook na execução se estiver claramente inválido."""
    global FB_ATIVO
    if not FB_ATIVO:
        return
    try:
        r = S.get(f"{FB_API}/me", params={"fields": "id,name", "access_token": FB_ACCESS_TOKEN}, timeout=20)
        if r.status_code != 200:
            code, _, msg = fb_erro(r)
            print(f"Facebook: token rejeitado (code {code}): {msg}")
            print("  →", fb_dica(code))
            FB_ATIVO = False
            return
        me = r.json()
        if str(me.get("id")) == FB_PAGE_ID:
            print(f"Facebook: token de PÁGINA ok — {me.get('name')}")
        else:
            print(f"Facebook: AVISO — o token pertence a '{me.get('name')}' (id {me.get('id')}), "
                  f"não à página {FB_PAGE_ID}. Se for token de usuário, troque pelo token da página (GET /me/accounts).")
        rp = S.get(f"{FB_API}/{FB_PAGE_ID}", params={"fields": "id,name", "access_token": FB_ACCESS_TOKEN}, timeout=20)
        if rp.status_code != 200:
            code, _, msg = fb_erro(rp)
            print(f"Facebook: não consegui acessar a página {FB_PAGE_ID} (code {code}): {msg}")
            print("  →", fb_dica(code))
            FB_ATIVO = False
        else:
            print(f"Facebook: página acessível — {rp.json().get('name')}")
    except Exception as e:
        print("Facebook: erro no diagnóstico:", e)


def postar_facebook(p, img=None):
    """Posta foto + legenda na página do Facebook via Graph API."""
    global FB_ATIVO, FB_FALHAS
    if not FB_ATIVO:
        return False
    caption = texto_post(p, para_facebook=True)
    try:
        if img:
            r = S.post(f"{FB_API}/{FB_PAGE_ID}/photos",
                       data={"caption": caption, "access_token": FB_ACCESS_TOKEN},
                       files={"source": ("oferta.jpg", img, "image/jpeg")}, timeout=90)
        elif p.get("imagem"):
            url = f"{FB_API}/{FB_PAGE_ID}/photos"
            data = {"url": p["imagem"], "caption": caption, "access_token": FB_ACCESS_TOKEN}
            r = S.post(url, data=data, timeout=40)
        else:
            url = f"{FB_API}/{FB_PAGE_ID}/feed"
            data = {"message": caption, "link": p["link"], "access_token": FB_ACCESS_TOKEN}
            r = S.post(url, data=data, timeout=30)
        if r.status_code in (200, 201):
            FB_FALHAS = 0
            print("Facebook:", r.status_code, r.text[:120])
            return True
        code, _, msg = fb_erro(r)
        print(f"Facebook: {r.status_code} (code {code}) {msg}")
        FB_FALHAS += 1
        if code in FB_ERROS_FATAIS:
            print("  →", fb_dica(code))
            print("  → Facebook desativado nesta execução (Telegram continua).")
            FB_ATIVO = False
        elif FB_FALHAS >= 3:
            print("  → 3 falhas seguidas no Facebook; desativado nesta execução.")
            FB_ATIVO = False
        return False
    except Exception as e:
        print("Erro Facebook:", e)
        FB_FALHAS += 1
        if FB_FALHAS >= 3:
            FB_ATIVO = False
        return False


def postar(p):
    """Gera o card uma vez e envia para todos os canais. Sucesso se pelo menos um canal funcionar."""
    img = gerar_card(p)
    ok_tg = postar_telegram(p, img) if (TG_TOKEN and TG_CHAT) else False
    ok_fb = postar_facebook(p, img) if FB_ATIVO else False
    return ok_tg or ok_fb


def pode_postar(agora):
    return 7 <= agora.hour <= 22


def salvar(estado):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    print(f"Meta: ML + Shopee | máx {MAX_POSTS} | bloqueio {REPETIR_APOS_DIAS}d | shopee={SHOPEE_POSTS} "
          f"| pool x{POOL_FATOR} | pausa {PAUSA_MIN}-{PAUSA_MAX}s | card={'sim' if (PIL_OK and IMG_CARD) else 'não'}")
    print(f"Canais: Telegram={'sim' if TG_TOKEN and TG_CHAT else 'não'} | Facebook={'sim' if FB_ATIVO else 'não'}")
    fb_diagnostico()

    h = headers_ml()
    estado = limpar_estado(carregar_estado(), agora)
    print(f"Bloqueados: {len(estado['ids'])} ids")

    pool_ml = montar_ml(h, estado) if h else []
    pool_sh = montar_shopee(estado)
    ranking = selecionar(pool_ml, pool_sh)
    ranking = [
        p for p in ranking
        if not ja_postado(estado, p["id"], p["titulo"], p.get("item_id"))
    ][:MAX_POSTS]
    print(f"Selecionados: {len(ranking)} (candidatos ML={len(pool_ml)} Shopee={len(pool_sh)})")

    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    itens = [{k: v for k, v in p.items() if k != "item_id" and not k.startswith("_")} for p in ranking]
    TRENDS.write_text(
        json.dumps({"atualizado": agora.isoformat(), "itens": itens},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not ranking:
        print("Nada a postar")
        salvar(estado)
        return

    tem_canal = (TG_TOKEN and TG_CHAT) or FB_ATIVO
    if not tem_canal:
        print("Nenhum canal ativo (Telegram ou Facebook)")
        salvar(estado)
        return

    if not pode_postar(agora):
        print(f"Fora do horário ({agora.hour}h)")
        salvar(estado)
        return

    enviados = 0
    for i, p in enumerate(ranking):
        if enviados >= MAX_POSTS:
            break
        if ja_postado(estado, p["id"], p["titulo"], p.get("item_id")):
            continue
        if postar(p):
            registrar(estado, p["id"], p["titulo"], agora, p.get("item_id"))
            enviados += 1
            salvar(estado)
        if i < len(ranking) - 1:                      # espaça os posts (evita rajada de notificações)
            espera = random.uniform(PAUSA_MIN, PAUSA_MAX)
            print(f"Aguardando {espera:.0f}s até o próximo post...")
            time.sleep(espera)

    salvar(estado)
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
