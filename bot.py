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

v3.4:
- Facebook: o post sai com foto + texto e o link de afiliado vai no PRIMEIRO COMENTÁRIO
  (exige token de página com pages_manage_engagement). Ligado por padrão;
  FB_LINK_NO_COMENTARIO=0 volta o link para a legenda.
- Se o comentário falhar, o bot edita a legenda para incluir o link e, no resto da
  execução, volta a postar com o link na legenda (nenhum post fica sem link).

v3.5 (afiliados):
- Link ML agora monta matt_tool + matt_word (quando informado).
- ML_AFFILIATE_ID e ML_AFFILIATE_WORD vêm de secrets (não mais hardcoded no workflow).
- Log do link gerado para facilitar debug de comissão.

v3.6 (Facebook ML engajamento):
- Posts do Mercado Livre no Facebook: legenda SEM o link.
  Texto pede "Comenta EU QUERO que eu te envio o link".
- O link NÃO é mais colocado automaticamente no 1º comentário para produtos ML
  (aumenta engajamento; você responde o comentário com o link).
- Shopee no Facebook continua com link no 1º comentário (comportamento anterior).
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


# --- Afiliado Mercado Livre -------------------------------------------------
# Use os secrets ML_AFFILIATE_ID e (opcional) ML_AFFILIATE_WORD.
# O painel oficial gera links curtos (mercadolivre.com/sec/...), mas este bot
# monta o parâmetro clássico matt_tool + matt_word, que ainda funciona na
# maioria dos casos. Se você tiver uma etiqueta (matt_word), configure-a.
AFF_ID = (os.getenv("ML_AFFILIATE_ID") or "71970283").strip()
AFF_WORD = (os.getenv("ML_AFFILIATE_WORD") or "").strip()  # etiqueta / tag (opcional)
# Se definir ML_AFFILIATE_PARAMS, ele tem prioridade (ex.: "matt_tool={id}&matt_word={word}")
AFF_PARAMS_TEMPLATE = (os.getenv("ML_AFFILIATE_PARAMS") or "").strip()

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
FB_LINK_NO_COMENTARIO = (os.getenv("FB_LINK_NO_COMENTARIO", "1").strip() != "0")  # link no 1º comentário (Facebook)

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
FB_COMENT_OK = True   # vira False se o comentário falhar → link volta para a legenda nesta execução

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
S.headers.update({"User-Agent": "radar-ml/3.6", "Accept": "application/json"})


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
    """Monta o link de afiliado do Mercado Livre.

    Prioridade:
    1. ML_AFFILIATE_PARAMS (template com {id} e {word})
    2. matt_tool=ID + matt_word=WORD (quando WORD estiver definido)
    3. só matt_tool=ID
    """
    if not AFF_ID:
        print("AVISO: ML_AFFILIATE_ID vazio — link sem rastreamento")
        return url

    if AFF_PARAMS_TEMPLATE:
        try:
            params = AFF_PARAMS_TEMPLATE.format(id=AFF_ID, word=AFF_WORD or AFF_ID)
        except Exception:
            params = f"matt_tool={AFF_ID}"
    else:
        parts = [f"matt_tool={AFF_ID}"]
        if AFF_WORD:
            parts.append(f"matt_word={AFF_WORD}")
        params = "&".join(parts)

    sep = "&" if "?" in url else "?"
    link = url + sep + params
    return link


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
    link = link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}")
    return {
        "id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
        "frete_gratis": frete, "categoria": label, "fonte": "ml",
        "link": link,
        "item_id": mid,
        "desconto": desc, "preco_original": orig,
        "vendidos": to_int(item.get("sold_quantity")),
    }


def ml_get(url, h, params=None):
    r = S.get(url, headers=h or {}, params=params, timeout=25)
    if r.status_code == 403 and h:
        r = S.get(url, headers={"Accept": "application/json", "User-Agent": "radar-ml/3.6"},
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
        for item in results:
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


def buscar_highlights(cat, h, vistos, vistos_t, n):
    """Highlights por categoria (fallback final)."""
    achados = []
    nome = CAT_NOMES.get(cat, cat)
    try:
        r = ml_get(f"{API}/highlights/MLB/category/{cat}", h)
        if r.status_code != 200:
            print(f"  highlights {nome}: {r.status_code}")
            return achados
        items = r.json().get("content") or []
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
                "link": link,  # offerLink já vem com o rastreamento da Shopee
                "item_id": str(node.get("itemId") or ""),
                "comissao": node.get("commissionRate"),
                "loja": node.get("shopName") or "",
                "termo": keyword,
                "desconto": desc,
                "preco_original": 0,
                "vendidos": to_int(node.get("sales")),
            }
            vistos.add(pid)
            if nt:
                vistos_t.add(nt)
            achados.append(p)
            print(f"  OK [Shopee] R$ {p['preco']:.2f} (-{p['desconto']}%) — {p['titulo'][:50]}")
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
    for p in ordenados:
        if len(escolhidos) >= n:
            break
        k = chave_variedade(p)
        if cont.get(k, 0) >= 2:
            continue
        cont[k] = cont.get(k, 0) + 1
        escolhidos.append(p)
    for p in ordenados:
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
    inter = [p for par in zip_longest(ml, sh) for p in par if p]
    if len(inter) < MAX_POSTS:
        sobra = [p for p in pool_ml + pool_sh if p not in inter]
        sobra.sort(key=score, reverse=True)
        inter += sobra[:MAX_POSTS - len(inter)]
    return inter[:MAX_POSTS]


# ----------------------------------------------------------------------------
# Texto dos posts
# ----------------------------------------------------------------------------
def texto_post(p, para_facebook=False, link_no_comentario=False):
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
        # ML: sem link na legenda → pede comentário "EU QUERO" (engajamento)
        # Shopee: mantém comportamento anterior (link no 1º comentário ou na legenda)
        if fonte == "ml":
            linha_link = "💬 Comenta EU QUERO que eu te envio o link 👇"
        else:
            linha_link = ("🔗 Link no primeiro comentário" if link_no_comentario
                          else f"👉 Ver oferta: {p['link']}")
        partes = [
            f"{gancho} | {titulo}",
            linha_preco,
        ]
        if extras:
            partes.append(" · ".join(extras))
        partes.append(linha_link)
        partes.append(hashtags(p, 4))
        partes.append(random.choice(PERGUNTAS_FB))
        return "\n".join(partes)

    # Telegram (HTML)
    linha_preco = f"💰 <b>{preco}</b>" + (f" <s>{preco_br(orig)}</s>" if orig else "")
    partes = [
        f"{gancho} — <b>{html.escape(titulo)}</b>",
        linha_preco,
    ]
    if extras:
        partes.append(" · ".join(html.escape(x) for x in extras))
    partes.append(f"🛒 {mkt}")
    partes.append(hashtags(p, 3))
    return "\n".join(partes)


# ----------------------------------------------------------------------------
# Imagem card (Pillow)
# ----------------------------------------------------------------------------
def baixar_imagem(url):
    if not url:
        return None
    try:
        r = S.get(url, timeout=20)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


def gerar_card(p):
    """Gera card visual com Pillow. Retorna bytes PNG ou None."""
    if not (PIL_OK and IMG_CARD):
        return None
    img_bytes = baixar_imagem(p.get("imagem"))
    if not img_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        # canvas
        W, H = 1080, 1080
        canvas = Image.new("RGB", (W, H), (245, 245, 245))
        # produto centralizado
        side = 720
        img = ImageOps.contain(img, (side, side))
        ox = (W - img.width) // 2
        oy = 80
        canvas.paste(img, (ox, oy))
        draw = ImageDraw.Draw(canvas)
        # fontes (fallback se não achar)
        try:
            font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
            font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        except Exception:
            font_big = font_med = font_sm = ImageFont.load_default()

        # selo desconto
        d = int(p.get("desconto") or 0)
        if d >= 5:
            badge = f"-{d}%"
            bw, bh = 160, 70
            bx, by = W - bw - 40, 40
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=16, fill=(220, 20, 60))
            draw.text((bx + 20, by + 12), badge, fill="white", font=font_med)

        # preço
        preco = preco_br(p["preco"])
        draw.text((40, H - 160), preco, fill=(20, 20, 20), font=font_big)
        if p.get("preco_original"):
            orig = preco_br(p["preco_original"])
            draw.text((40, H - 90), f"De {orig}", fill=(120, 120, 120), font=font_sm)

        if BRAND_NAME:
            draw.text((W - 40, H - 50), BRAND_NAME, fill=(100, 100, 100), font=font_sm, anchor="rb")

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        print("Card falhou:", e)
        return None


# ----------------------------------------------------------------------------
# Canais de postagem
# ----------------------------------------------------------------------------
def postar_telegram(p):
    if not (TG_TOKEN and TG_CHAT):
        return False
    texto = texto_post(p, para_facebook=False)
    card = gerar_card(p)
    try:
        if card:
            files = {"photo": ("card.png", card, "image/png")}
            data = {
                "chat_id": TG_CHAT,
                "caption": texto,
                "parse_mode": "HTML",
                "reply_markup": json.dumps({
                    "inline_keyboard": [[{ "text": "🛒 Ver oferta", "url": p["link"] }]]
                }),
            }
            r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                       data=data, files=files, timeout=40)
        else:
            # fallback texto + link
            r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={
                "chat_id": TG_CHAT,
                "text": texto + f"\n\n👉 {p['link']}",
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }, timeout=30)
        if r.status_code == 200:
            print(f"  Telegram OK — {p['titulo'][:40]}")
            return True
        print(f"  Telegram falhou: {r.status_code} {r.text[:150]}")
    except Exception as e:
        print("  Telegram erro:", e)
    return False


def postar_facebook(p):
    global FB_FALHAS, FB_COMENT_OK, FB_ATIVO
    if not FB_ATIVO:
        return False
    if FB_FALHAS >= 3:
        print("  Facebook desligado nesta execução (muitas falhas)")
        return False

    # ML no Facebook: NÃO coloca link no 1º comentário (legenda já pede "EU QUERO")
    # Shopee: mantém link no 1º comentário se a flag estiver ligada
    eh_ml = (p.get("fonte") or "ml") == "ml"
    usar_comentario = (not eh_ml) and FB_LINK_NO_COMENTARIO and FB_COMENT_OK
    texto = texto_post(p, para_facebook=True, link_no_comentario=usar_comentario)
    card = gerar_card(p)
    foto_url = p.get("imagem") or ""

    try:
        if card:
            # upload da foto gerada
            files = {"source": ("card.png", card, "image/png")}
            data = {
                "caption": texto,
                "access_token": FB_ACCESS_TOKEN,
            }
            r = S.post(f"{FB_API}/{FB_PAGE_ID}/photos", data=data, files=files, timeout=40)
        else:
            # foto original via URL
            r = S.post(f"{FB_API}/{FB_PAGE_ID}/photos", data={
                "url": foto_url,
                "caption": texto,
                "access_token": FB_ACCESS_TOKEN,
            }, timeout=40)

        if r.status_code != 200:
            err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            code = (err.get("error") or {}).get("code")
            print(f"  Facebook foto falhou: {r.status_code} {str(err)[:200]}")
            FB_FALHAS += 1
            if code in FB_ERROS_FATAIS:
                FB_ATIVO = False
            return False

        post_id = (r.json() or {}).get("post_id") or (r.json() or {}).get("id")
        print(f"  Facebook OK — {p['titulo'][:40]}")
        if eh_ml:
            print("  → ML: legenda pede EU QUERO (link não vai no comentário)")

        # link no primeiro comentário (somente Shopee / quando usar_comentario=True)
        if usar_comentario and post_id:
            try:
                rc = S.post(f"{FB_API}/{post_id}/comments", data={
                    "message": f"🔗 Link da oferta:\n{p['link']}",
                    "access_token": FB_ACCESS_TOKEN,
                }, timeout=20)
                if rc.status_code == 200:
                    print("  → link no comentário OK")
                else:
                    print(f"  → comentário falhou ({rc.status_code}), editando legenda...")
                    FB_COMENT_OK = False
                    # tenta editar a legenda para incluir o link
                    S.post(f"{FB_API}/{post_id}", data={
                        "message": texto + f"\n\n👉 {p['link']}",
                        "access_token": FB_ACCESS_TOKEN,
                    }, timeout=20)
            except Exception as e:
                print("  → comentário erro:", e)
                FB_COMENT_OK = False
        return True
    except Exception as e:
        print("  Facebook erro:", e)
        FB_FALHAS += 1
        return False


def postar(p):
    ok_tg = postar_telegram(p)
    ok_fb = postar_facebook(p)
    if ok_tg or ok_fb:
        print(f"  LINK AFILIADO → {p['link']}")
        return True
    return False


def fb_diagnostico():
    if not FB_ATIVO:
        print("Facebook: inativo (sem FB_PAGE_ID / FB_ACCESS_TOKEN)")
        return
    try:
        r = S.get(f"{FB_API}/{FB_PAGE_ID}", params={
            "fields": "name,id",
            "access_token": FB_ACCESS_TOKEN,
        }, timeout=15)
        if r.status_code == 200:
            print(f"Facebook: página OK — {(r.json() or {}).get('name')}")
        else:
            print(f"Facebook: token/página com problema ({r.status_code})")
    except Exception as e:
        print("Facebook diagnóstico:", e)


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
    print(f"Afiliado ML: matt_tool={AFF_ID}" + (f" + matt_word={AFF_WORD}" if AFF_WORD else ""))
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
        if i < len(ranking) - 1:
            espera = random.uniform(PAUSA_MIN, PAUSA_MAX)
            print(f"Aguardando {espera:.0f}s até o próximo post...")
            time.sleep(espera)

    salvar(estado)
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
