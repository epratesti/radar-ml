"""Radar Shopee → Telegram + Facebook Page.

v4.0 (somente Shopee)

Melhorias desta versão:
- Removido o Mercado Livre (o bot não depende mais dos secrets ML).
- Layout novo: título em negrito, preço, % OFF, vendidos, loja e botão "Comprar".
- Preço "a partir de" quando o produto tem variações com preços diferentes.
- Títulos limpos (sem espaços duplos, sem CAIXA ALTA, cortados sem quebrar palavra).
- Telegram: se a foto falhar, envia como texto (o post não se perde) + tratamento de 429.
- Filtro de qualidade: ignora produtos com poucas vendas (SHOPEE_MIN_VENDAS).
- Para de consultar a Shopee após 3 falhas seguidas (economiza tempo no Actions).
- Facebook: diagnóstico do token e desativa na execução após erro fatal.
"""

import os, json, html, re, random, hashlib, time, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


def env_int(nome, padrao):
    try:
        return int(os.getenv(nome, "").strip() or padrao)
    except ValueError:
        return padrao


TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()

MAX_POSTS = env_int("MAX_POSTS", 8)
REPETIR_APOS_DIAS = env_int("REPETIR_APOS_DIAS", 3)
SHOPEE_MIN_VENDAS = env_int("SHOPEE_MIN_VENDAS", 20)  # use 0 para não filtrar

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()
SHOPEE_GQL = "https://open-api.affiliate.shopee.com.br/graphql"

FB_API = "https://graph.facebook.com/v21.0"

ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

# Estado de execução (circuit breakers)
FB_ATIVO = bool(FB_PAGE_ID and FB_ACCESS_TOKEN)
FB_FALHAS = 0
FB_ERROS_FATAIS = {3, 10, 102, 190, 200, 240, 283, 368}
SHOPEE_FALHAS = 0
SHOPEE_OFF = False

SHOPEE_QUERIES = [
    "fone bluetooth", "smartwatch", "carregador turbo", "power bank",
    "air fryer", "liquidificador", "aspirador", "cafeteira",
    "tenis", "mochila", "relogio", "chinelo",
    "whey protein", "perfume", "kit skincare", "garrafa termica",
    "caixa de som", "mouse gamer", "organizador", "ventilador",
]

S = requests.Session()
S.headers.update({"User-Agent": "radar-shopee/4.0", "Accept": "application/json"})


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def num(v, padrao=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def norm(t):
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9áéíóúâêôãõç\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:80]


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def cortar(t, n=90):
    """Limpa espaços, converte CAIXA ALTA e corta sem quebrar palavra."""
    t = " ".join((t or "").split())
    letras = [c for c in t if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.7:
        t = t.lower().capitalize()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(" ,.-–|/") + "…"


def fmt_vendas(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 1000:
        return f"{n / 1000:.1f} mil".replace(".", ",").replace(",0 ", " ")
    return str(n)


# ----------------------------------------------------------------------------
# Estado (anti-repetição)
# ----------------------------------------------------------------------------
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


def salvar(estado):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------
# Shopee
# ----------------------------------------------------------------------------
def shopee_request(query_str):
    payload = json.dumps({"query": query_str}, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()))
    factor = f"{SHOPEE_APP_ID}{ts}{payload}{SHOPEE_SECRET}"
    sig = hashlib.sha256(factor.encode("utf-8")).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={ts}, Signature={sig}",
    }
    return S.post(SHOPEE_GQL, data=payload.encode("utf-8"), headers=headers, timeout=30)


def buscar_shopee(keyword, vistos, vistos_t, n):
    global SHOPEE_FALHAS, SHOPEE_OFF
    achados = []
    if SHOPEE_OFF:
        return achados

    kw = json.dumps(keyword, ensure_ascii=False)
    q = (
        "{"
        f"productOfferV2(keyword: {kw}, listType: 0, sortType: 2, page: 1, limit: 30) {{"
        "nodes { itemId productName offerLink productLink imageUrl priceMin priceMax "
        "priceDiscountRate sales commissionRate shopName }"
        " pageInfo { page limit hasNextPage }"
        "}}"
    )
    try:
        r = shopee_request(q)
        if r.status_code != 200:
            print(f"  Shopee '{keyword}': HTTP {r.status_code} {r.text[:180]}")
            SHOPEE_FALHAS += 1
        else:
            data = r.json()
            if data.get("errors"):
                print(f"  Shopee errors: {str(data['errors'])[:200]}")
                SHOPEE_FALHAS += 1
            else:
                SHOPEE_FALHAS = 0
                nodes = ((data.get("data") or {}).get("productOfferV2") or {}).get("nodes") or []
                random.shuffle(nodes)
                for node in nodes:
                    if len(achados) >= n:
                        break
                    p = montar_produto(node, vistos, vistos_t)
                    if p:
                        achados.append(p)
                        print(f"  OK [Shopee] {preco_br(p['preco'])} — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro Shopee '{keyword}':", e)
        SHOPEE_FALHAS += 1

    if SHOPEE_FALHAS >= 3:
        SHOPEE_OFF = True
        print("  → 3 falhas seguidas na Shopee; parando as buscas nesta execução.")
    return achados


def montar_produto(node, vistos, vistos_t):
    titulo = " ".join((node.get("productName") or "").split())
    link = node.get("offerLink") or node.get("productLink") or ""
    item_id = str(node.get("itemId") or "")
    pid = f"SHOPEE-{item_id}"

    pmin = num(node.get("priceMin"))
    pmax = num(node.get("priceMax"))
    preco = pmin or pmax
    if not titulo or not link or not item_id or preco <= 0:
        return None

    vendas = int(num(node.get("sales")))
    if SHOPEE_MIN_VENDAS and vendas < SHOPEE_MIN_VENDAS:
        return None

    desconto = int(num(node.get("priceDiscountRate")))
    if not (0 < desconto < 90):
        desconto = 0

    nt = norm(titulo)
    if pid in vistos or item_id in vistos or (nt and nt in vistos_t):
        return None

    vistos.add(pid)
    vistos.add(item_id)
    if nt:
        vistos_t.add(nt)

    return {
        "id": pid,
        "titulo": titulo,
        "preco": preco,
        "a_partir_de": pmax > pmin > 0,
        "desconto": desconto,
        "vendas": vendas,
        "imagem": node.get("imageUrl") or "",
        "frete_gratis": False,
        "categoria": "Shopee",
        "fonte": "shopee",
        "link": link,
        "item_id": item_id,
        "comissao": node.get("commissionRate"),
        "loja": node.get("shopName") or "",
    }


def montar_shopee(estado):
    if not (SHOPEE_APP_ID and SHOPEE_SECRET):
        print("ERRO: secrets SHOPEE_APP_ID / SHOPEE_SECRET ausentes")
        return []
    print("Shopee: buscando ofertas de afiliado...")
    vistos = set(str(k) for k in estado["ids"])
    vistos_t = set(estado["titulos"])
    out = []
    qs = list(SHOPEE_QUERIES)
    random.shuffle(qs)
    for q in qs:
        if len(out) >= MAX_POSTS or SHOPEE_OFF:
            break
        falta = MAX_POSTS - len(out)
        out.extend(buscar_shopee(q, vistos, vistos_t, min(2, falta)))
        time.sleep(0.4)
    return out[:MAX_POSTS]


# ----------------------------------------------------------------------------
# Texto dos posts
# ----------------------------------------------------------------------------
def linhas_info(p, html_mode):
    esc = html.escape if html_mode else (lambda x: x)
    linhas = []
    if p.get("desconto"):
        linhas.append(f"🔥 {p['desconto']}% OFF")
    if p.get("vendas"):
        linhas.append(f"⭐ {fmt_vendas(p['vendas'])} vendidos")
    if p.get("loja"):
        linhas.append(f"🏪 {esc(cortar(p['loja'], 40))}")
    return linhas


def texto_post(p, para_facebook=False):
    """Telegram usa HTML; Facebook usa texto puro."""
    titulo = cortar(p["titulo"], 90)
    prefixo = "a partir de " if p.get("a_partir_de") else ""
    preco = preco_br(p["preco"])
    rodape = "Link de afiliado · preço e estoque podem variar"

    if para_facebook:
        info = "\n".join(linhas_info(p, False))
        return (
            f"🛍️ {titulo}\n\n"
            f"💰 {prefixo}{preco}\n"
            f"{info}\n\n"
            f"👉 Comprar na Shopee: {p['link']}\n\n"
            f"{rodape}"
        ).replace("\n\n\n", "\n\n")

    info = "\n".join(linhas_info(p, True))
    return (
        f"🛍️ <b>{html.escape(titulo)}</b>\n\n"
        f"💰 {prefixo}<b>{preco}</b>\n"
        f"{info}\n\n"
        f"<i>{rodape}</i>"
    ).replace("\n\n\n", "\n\n")


# ----------------------------------------------------------------------------
# Canais
# ----------------------------------------------------------------------------
def tg_enviar(metodo, data):
    r = None
    for _ in range(2):
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/{metodo}", data=data, timeout=30)
        if r.status_code == 429:
            try:
                espera = int(r.json().get("parameters", {}).get("retry_after", 3))
            except Exception:
                espera = 3
            time.sleep(min(espera, 30))
            continue
        break
    return r


def postar_telegram(p):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado")
        return False

    texto = texto_post(p, para_facebook=False)
    botao = json.dumps({"inline_keyboard": [[{"text": "🛒 Comprar na Shopee", "url": p["link"]}]]})
    base = {"chat_id": TG_CHAT, "parse_mode": "HTML", "reply_markup": botao}

    r = None
    if p.get("imagem"):
        r = tg_enviar("sendPhoto", {**base, "photo": p["imagem"], "caption": texto})
        if r is not None and not r.ok:
            print("Telegram: foto falhou, enviando só texto:", r.text[:120])
            r = None

    if r is None:
        r = tg_enviar("sendMessage", {**base, "text": texto, "disable_web_page_preview": "true"})

    print("Telegram:", r.status_code, r.text[:100])
    return r.ok


def fb_erro(r):
    try:
        e = (r.json() or {}).get("error") or {}
        return e.get("code"), e.get("error_subcode"), e.get("message") or r.text[:180]
    except Exception:
        return None, None, r.text[:180]


def fb_dica(code):
    if code == 240:
        return ("Token sem permissão de postar nessa página. Use o PAGE ACCESS TOKEN da página "
                "(GET /me/accounts), com pages_manage_posts, e confira o FB_PAGE_ID.")
    if code in (190, 102):
        return "Token inválido ou expirado. Gere um novo token de página (longa duração)."
    if code in (10, 200, 283, 368):
        return "Falta permissão (pages_manage_posts), app em modo Desenvolvimento ou conta restrita."
    if code == 3:
        return "Funcionalidade indisponível para esse app/token. Revise as permissões."
    return "Verifique FB_PAGE_ID, FB_ACCESS_TOKEN e as permissões do app."


def fb_diagnostico():
    global FB_ATIVO
    if not FB_ATIVO:
        return
    try:
        r = S.get(f"{FB_API}/me", params={"fields": "id,name", "access_token": FB_ACCESS_TOKEN}, timeout=20)
        if r.status_code != 200:
            code, _, msg = fb_erro(r)
            print(f"Facebook: token rejeitado (code {code}): {msg}")
            print(" →", fb_dica(code))
            FB_ATIVO = False
            return
        me = r.json()
        if str(me.get("id")) == FB_PAGE_ID:
            print(f"Facebook: token de PÁGINA ok — {me.get('name')}")
        else:
            print(f"Facebook: AVISO — token pertence a '{me.get('name')}' (id {me.get('id')}), "
                  f"não à página {FB_PAGE_ID}. Use o token da página.")
    except Exception as e:
        print("Facebook: erro no diagnóstico:", e)


def postar_facebook(p):
    global FB_ATIVO, FB_FALHAS
    if not FB_ATIVO:
        return False
    caption = texto_post(p, para_facebook=True)
    try:
        if p.get("imagem"):
            r = S.post(f"{FB_API}/{FB_PAGE_ID}/photos",
                       data={"url": p["imagem"], "caption": caption, "access_token": FB_ACCESS_TOKEN},
                       timeout=40)
        else:
            r = S.post(f"{FB_API}/{FB_PAGE_ID}/feed",
                       data={"message": caption, "link": p["link"], "access_token": FB_ACCESS_TOKEN},
                       timeout=30)

        if r.status_code in (200, 201):
            FB_FALHAS = 0
            print("Facebook:", r.status_code, r.text[:100])
            return True

        code, _, msg = fb_erro(r)
        print(f"Facebook: {r.status_code} (code {code}) {msg}")
        FB_FALHAS += 1
        if code in FB_ERROS_FATAIS:
            print(" →", fb_dica(code))
            print(" → Facebook desativado nesta execução (Telegram continua).")
            FB_ATIVO = False
        elif FB_FALHAS >= 3:
            print(" → 3 falhas seguidas; Facebook desativado nesta execução.")
            FB_ATIVO = False
        return False
    except Exception as e:
        print("Erro Facebook:", e)
        FB_FALHAS += 1
        if FB_FALHAS >= 3:
            FB_ATIVO = False
        return False


def postar(p):
    """Sucesso se pelo menos um canal funcionar."""
    ok_tg = postar_telegram(p) if (TG_TOKEN and TG_CHAT) else False
    ok_fb = postar_facebook(p) if FB_ATIVO else False
    return ok_tg or ok_fb


def pode_postar(agora):
    return 7 <= agora.hour <= 22


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    print(f"Meta: Shopee | máx {MAX_POSTS} | bloqueio {REPETIR_APOS_DIAS}d | min vendas {SHOPEE_MIN_VENDAS}")
    print(f"Canais: Telegram={'sim' if TG_TOKEN and TG_CHAT else 'não'} | Facebook={'sim' if FB_ATIVO else 'não'}")

    fb_diagnostico()

    estado = limpar_estado(carregar_estado(), agora)
    print(f"Bloqueados: {len(estado['ids'])} ids")

    ranking = montar_shopee(estado)
    random.shuffle(ranking)
    ranking = [
        p for p in ranking
        if not ja_postado(estado, p["id"], p["titulo"], p.get("item_id"))
    ][:MAX_POSTS]
    print(f"Selecionados: {len(ranking)}")

    # trends.json (usado pelo site em docs/) — campos antigos mantidos, novos adicionados
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    itens = [{k: v for k, v in p.items() if k != "item_id"} for p in ranking]
    TRENDS.write_text(
        json.dumps({"atualizado": agora.isoformat(), "itens": itens}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not ranking:
        print("Nada a postar")
        salvar(estado)
        return

    if not ((TG_TOKEN and TG_CHAT) or FB_ATIVO):
        print("Nenhum canal ativo (Telegram ou Facebook)")
        salvar(estado)
        return

    if not pode_postar(agora):
        print(f"Fora do horário ({agora.hour}h)")
        salvar(estado)
        return

    enviados = 0
    for p in ranking:
        if enviados >= MAX_POSTS:
            break
        if postar(p):
            registrar(estado, p["id"], p["titulo"], agora, p.get("item_id"))
            enviados += 1
            salvar(estado)
        time.sleep(1.5)

    salvar(estado)
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
