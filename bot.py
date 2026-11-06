"""Radar Shopee → Telegram + Facebook Page.

v5.0 (somente Shopee)

Novidades:
1. Ranking por potencial: pontua vendas, desconto e comissão (não é mais aleatório).
2. Queda de preço: guarda o último preço postado e reposta se baixar (QUEDA_MIN_PCT).
3. Posts espalhados: poucos posts por execução, com intervalo entre eles.
4. Textos variados (várias chamadas) + hashtags no Facebook.
5. Alertas privados no Telegram quando algo falha (ALERTA_CHAT_ID).
6. docs/trends.json acumula os últimos posts (o site não fica com só 2 itens).
7. DRY_RUN=1: mostra os posts no log sem enviar nem salvar nada.
"""

import os, json, html, re, math, random, hashlib, time, unicodedata, traceback
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


def env_int(nome, padrao):
    try:
        return int(os.getenv(nome, "").strip() or padrao)
    except ValueError:
        return padrao


# --- Canais -----------------------------------------------------------------
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
ALERTA_CHAT_ID = os.getenv("ALERTA_CHAT_ID", "").strip()  # SEU chat privado (não o canal)
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()

# --- Shopee -----------------------------------------------------------------
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()
SHOPEE_GQL = "https://open-api.affiliate.shopee.com.br/graphql"

# --- Ajustes ----------------------------------------------------------------
MAX_POSTS = env_int("MAX_POSTS", 2)                      # posts por execução
QUERIES_POR_EXECUCAO = env_int("QUERIES_POR_EXECUCAO", 6)
CANDIDATOS_POR_BUSCA = env_int("CANDIDATOS_POR_BUSCA", 8)
SHOPEE_MIN_VENDAS = env_int("SHOPEE_MIN_VENDAS", 20)     # 0 = não filtrar
REPETIR_APOS_DIAS = env_int("REPETIR_APOS_DIAS", 3)
QUEDA_MIN_PCT = env_int("QUEDA_MIN_PCT", 10)             # queda mínima p/ repostar
PRECO_HISTORICO_DIAS = env_int("PRECO_HISTORICO_DIAS", 14)
POST_INTERVALO_SEG = env_int("POST_INTERVALO_SEG", 30)
ALERTA_REPETIR_HORAS = env_int("ALERTA_REPETIR_HORAS", 12)
TRENDS_MAX = env_int("TRENDS_MAX", 30)
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "sim", "yes")

FB_API = "https://graph.facebook.com/v21.0"

ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

# --- Estado de execução (circuit breakers e alertas) --------------------------
FB_ATIVO = bool(FB_PAGE_ID and FB_ACCESS_TOKEN)
FB_FALHAS = 0
FB_ERROS_FATAIS = {3, 10, 102, 190, 200, 240, 283, 368}
SHOPEE_FALHAS = 0
SHOPEE_OFF = False
SHOPEE_ULTIMO_ERRO = ""
ALERTAS = {}  # chave -> mensagem

SHOPEE_QUERIES = [
    "fone bluetooth", "smartwatch", "carregador turbo", "power bank",
    "air fryer", "liquidificador", "aspirador", "cafeteira",
    "tenis", "mochila", "relogio", "chinelo",
    "whey protein", "perfume", "kit skincare", "garrafa termica",
    "caixa de som", "mouse gamer", "organizador", "ventilador",
]

CHAMADAS = [
    "🔥 Oferta do dia",
    "💸 Preço bom na Shopee",
    "⚡ Corre que tá barato",
    "🛍️ Achadinho Shopee",
    "🎯 Olha esse preço",
    "⭐ Mais vendido da Shopee",
]

S = requests.Session()
S.headers.update({"User-Agent": "radar-shopee/5.0", "Accept": "application/json"})


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


def hashtag(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]", "", t)
    return f"#{t}" if t else ""


def add_alerta(chave, msg):
    if chave not in ALERTAS:
        ALERTAS[chave] = msg
    print(f"⚠️ ALERTA [{chave}]: {msg}")


# ----------------------------------------------------------------------------
# Estado (anti-repetição + histórico de preços + alertas)
# ----------------------------------------------------------------------------
def estado_vazio():
    return {"ids": {}, "titulos": {}, "precos": {}, "alertas": {}}


def carregar_estado():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(STATE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            return estado_vazio()
        if data and "ids" not in data:  # formato antigo (só ids)
            data = {"ids": data}
        for k in ("ids", "titulos", "precos", "alertas"):
            data.setdefault(k, {})
        return data
    except Exception:
        return estado_vazio()


def _depois_de(ts, limite):
    try:
        return dt.datetime.fromisoformat(ts) > limite
    except Exception:
        return False


def limpar_estado(estado, agora):
    lim = agora - dt.timedelta(days=REPETIR_APOS_DIAS)
    lim_preco = agora - dt.timedelta(days=PRECO_HISTORICO_DIAS)
    lim_alerta = agora - dt.timedelta(days=2)
    return {
        "ids": {k: v for k, v in estado.get("ids", {}).items() if _depois_de(v, lim)},
        "titulos": {k: v for k, v in estado.get("titulos", {}).items() if _depois_de(v, lim)},
        "precos": {k: v for k, v in estado.get("precos", {}).items()
                   if isinstance(v, dict) and _depois_de(v.get("ts", ""), lim_preco)},
        "alertas": {k: v for k, v in estado.get("alertas", {}).items() if _depois_de(v, lim_alerta)},
    }


def registrar(estado, p, agora):
    ts = agora.isoformat()
    estado["ids"][str(p["id"])] = ts
    if p.get("item_id"):
        estado["ids"][str(p["item_id"])] = ts
    nt = norm(p["titulo"])
    if nt:
        estado["titulos"][nt] = ts
    estado["precos"][str(p["id"])] = {"preco": p["preco"], "ts": ts}


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


def montar_produto(node, estado, ctx, busca):
    """Converte um nó da API em produto. Retorna None se não for elegível."""
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

    comissao = num(node.get("commissionRate"))
    comissao_pct = comissao * 100 if comissao <= 1 else comissao

    nt = norm(titulo)

    # duplicado dentro desta mesma execução
    if pid in ctx["run"] or (nt and nt in ctx["run_t"]):
        return None

    # queda de preço: produto já postado que ficou mais barato
    preco_anterior = None
    ant = estado["precos"].get(pid)
    if ant:
        p_ant = num(ant.get("preco"))
        if p_ant > 0 and preco <= p_ant * (1 - QUEDA_MIN_PCT / 100):
            try:
                ha_mais_de_1_dia = (dt.datetime.now(TZ) - dt.datetime.fromisoformat(ant["ts"])) >= dt.timedelta(days=1)
            except Exception:
                ha_mais_de_1_dia = False
            if ha_mais_de_1_dia:
                preco_anterior = p_ant

    # bloqueio por já ter postado (não vale para queda de preço)
    if not preco_anterior:
        if pid in ctx["vistos"] or item_id in ctx["vistos"] or (nt and nt in ctx["vistos_t"]):
            return None

    ctx["run"].add(pid)
    if nt:
        ctx["run_t"].add(nt)

    p = {
        "id": pid,
        "titulo": titulo,
        "preco": preco,
        "preco_anterior": preco_anterior,
        "a_partir_de": pmax > pmin > 0,
        "desconto": desconto,
        "vendas": vendas,
        "imagem": node.get("imageUrl") or "",
        "frete_gratis": False,
        "categoria": "Shopee",
        "busca": busca,
        "fonte": "shopee",
        "link": link,
        "item_id": item_id,
        "comissao": node.get("commissionRate"),
        "comissao_pct": comissao_pct,
        "loja": node.get("shopName") or "",
        "chamada": random.choice(CHAMADAS),
    }
    p["score"] = pontuar(p)
    return p


def pontuar(p):
    """Quanto maior, mais vale postar: vendas + desconto + comissão (+ bônus de queda)."""
    s = math.log1p(p["vendas"])
    s += p["desconto"] * 0.08
    s += min(p["comissao_pct"], 30) * 0.3
    if p.get("preco_anterior"):
        s += 4
    return s + random.uniform(0, 1.5)  # um pouco de aleatoriedade p/ variar


def buscar_shopee(keyword, estado, ctx):
    global SHOPEE_FALHAS, SHOPEE_OFF, SHOPEE_ULTIMO_ERRO
    achados = []
    if SHOPEE_OFF:
        return achados

    kw = json.dumps(keyword, ensure_ascii=False)
    q = (
        "{"
        f"productOfferV2(keyword: {kw}, listType: 0, sortType: 2, page: 1, limit: 40) {{"
        "nodes { itemId productName offerLink productLink imageUrl priceMin priceMax "
        "priceDiscountRate sales commissionRate shopName }"
        " pageInfo { page limit hasNextPage }"
        "}}"
    )
    try:
        r = shopee_request(q)
        if r.status_code != 200:
            SHOPEE_ULTIMO_ERRO = f"HTTP {r.status_code} {r.text[:120]}"
            print(f"  Shopee '{keyword}': {SHOPEE_ULTIMO_ERRO}")
            SHOPEE_FALHAS += 1
        else:
            data = r.json()
            if data.get("errors"):
                SHOPEE_ULTIMO_ERRO = str(data["errors"])[:160]
                print(f"  Shopee errors: {SHOPEE_ULTIMO_ERRO}")
                SHOPEE_FALHAS += 1
            else:
                SHOPEE_FALHAS = 0
                nodes = ((data.get("data") or {}).get("productOfferV2") or {}).get("nodes") or []
                for node in nodes:  # já vem ordenado por vendas
                    if len(achados) >= CANDIDATOS_POR_BUSCA:
                        break
                    p = montar_produto(node, estado, ctx, keyword)
                    if p:
                        achados.append(p)
                print(f"  '{keyword}': {len(achados)} candidatos")
    except Exception as e:
        SHOPEE_ULTIMO_ERRO = f"{type(e).__name__}: {e}"
        print(f"Erro Shopee '{keyword}':", e)
        SHOPEE_FALHAS += 1

    if SHOPEE_FALHAS >= 3 and not SHOPEE_OFF:
        SHOPEE_OFF = True
        add_alerta("shopee_off", f"Shopee falhou 3x seguidas ({SHOPEE_ULTIMO_ERRO}). Buscas interrompidas.")
    return achados


def coletar_shopee(estado):
    if not (SHOPEE_APP_ID and SHOPEE_SECRET):
        add_alerta("shopee_secrets", "Secrets SHOPEE_APP_ID / SHOPEE_SECRET ausentes.")
        return []
    print("Shopee: coletando candidatos...")
    ctx = {
        "vistos": set(str(k) for k in estado["ids"]),
        "vistos_t": set(estado["titulos"]),
        "run": set(),
        "run_t": set(),
    }
    qs = list(SHOPEE_QUERIES)
    random.shuffle(qs)
    pool = []
    for q in qs[:QUERIES_POR_EXECUCAO]:
        if SHOPEE_OFF:
            break
        pool.extend(buscar_shopee(q, estado, ctx))
        time.sleep(0.4)
    if not pool and not SHOPEE_OFF:
        add_alerta("sem_candidatos",
                   "Nenhum produto elegível encontrado (filtros de vendas/duplicados muito rígidos?).")
    return pool


def escolher(pool, n):
    """Pega os melhores por pontuação, limitando quantos vêm da mesma busca."""
    limite_por_busca = max(1, (n + 2) // 3)
    por_busca, out = {}, []
    for p in sorted(pool, key=lambda x: x["score"], reverse=True):
        if len(out) >= n:
            break
        if por_busca.get(p["busca"], 0) >= limite_por_busca:
            continue
        por_busca[p["busca"]] = por_busca.get(p["busca"], 0) + 1
        out.append(p)
    return out


# ----------------------------------------------------------------------------
# Texto dos posts
# ----------------------------------------------------------------------------
def texto_post(p, para_facebook=False):
    """Telegram usa HTML; Facebook usa texto puro (com hashtags)."""
    fb = para_facebook
    esc = (lambda x: x) if fb else html.escape

    titulo = esc(cortar(p["titulo"], 90))
    prefixo = "a partir de " if p.get("a_partir_de") else ""
    preco = preco_br(p["preco"])
    ant = p.get("preco_anterior")
    rodape = "Link de afiliado · preço e estoque podem variar"

    if ant:
        pct = max(1, round((1 - p["preco"] / ant) * 100))
        chamada = "🔻 BAIXOU DE PREÇO"
        if fb:
            preco_linha = f"💰 De {preco_br(ant)} por {prefixo}{preco} (-{pct}%)"
        else:
            preco_linha = f"💰 <s>{preco_br(ant)}</s> ➜ {prefixo}<b>{preco}</b> (-{pct}%)"
    else:
        chamada = p.get("chamada") or CHAMADAS[0]
        preco_linha = f"💰 {prefixo}{preco}" if fb else f"💰 {prefixo}<b>{preco}</b>"

    info = []
    if p.get("desconto") and not ant:
        info.append(f"🔥 {p['desconto']}% OFF")
    if p.get("vendas"):
        info.append(f"⭐ {fmt_vendas(p['vendas'])} vendidos")
    if p.get("loja"):
        info.append(f"🏪 {esc(cortar(p['loja'], 40))}")

    if fb:
        tags = " ".join(t for t in ["#Shopee", "#Ofertas", "#Promoção", hashtag(p.get("busca"))] if t)
        linhas = [chamada, "", titulo, "", preco_linha] + info + [
            "", f"👉 Comprar na Shopee: {p['link']}", "", rodape, "", tags,
        ]
    else:
        linhas = [chamada, "", f"<b>{titulo}</b>", "", preco_linha] + info + ["", f"<i>{rodape}</i>"]
    return "\n".join(linhas)


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
    if not r.ok:
        add_alerta("tg_falha", f"Telegram recusou o post (HTTP {r.status_code}): {r.text[:120]}")
    return r.ok


def enviar_privado(texto):
    """Aviso só para você (ALERTA_CHAT_ID). Nunca usa o canal público."""
    if not (TG_TOKEN and ALERTA_CHAT_ID):
        print("(aviso não enviado: defina o secret ALERTA_CHAT_ID)")
        return False
    try:
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                   data={"chat_id": ALERTA_CHAT_ID, "text": texto, "parse_mode": "HTML"}, timeout=20)
        return r.ok
    except Exception as e:
        print("Erro ao enviar aviso:", e)
        return False


def enviar_alertas(estado, agora):
    """Envia avisos acumulados, sem repetir o mesmo problema dentro de ALERTA_REPETIR_HORAS."""
    if not ALERTAS:
        return
    lim = agora - dt.timedelta(hours=ALERTA_REPETIR_HORAS)
    novos = [(k, m) for k, m in ALERTAS.items() if not _depois_de(estado["alertas"].get(k, ""), lim)]
    if not novos:
        return
    texto = "⚠️ <b>Radar Shopee</b>\n\n" + "\n".join(f"• {html.escape(m)}" for _, m in novos)
    if enviar_privado(texto):
        for k, _ in novos:
            estado["alertas"][k] = agora.isoformat()
        salvar(estado)


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
            add_alerta("fb_off", f"Facebook desativado (code {code}): {fb_dica(code)}")
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
            add_alerta("fb_off", f"Facebook desativado (code {code}): {fb_dica(code)}")
            FB_ATIVO = False
        elif FB_FALHAS >= 3:
            add_alerta("fb_off", "Facebook falhou 3x seguidas e foi desativado nesta execução.")
            FB_ATIVO = False
        return False
    except Exception as e:
        print("Erro Facebook:", e)
        FB_FALHAS += 1
        if FB_FALHAS >= 3:
            add_alerta("fb_off", f"Facebook com erro de conexão repetido ({e}).")
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
# Site (docs/trends.json) — acumula os últimos posts
# ----------------------------------------------------------------------------
def atualizar_trends(postados, agora):
    ocultar = {"item_id", "chamada", "score", "comissao_pct"}
    antigos = []
    try:
        antigos = (json.loads(TRENDS.read_text(encoding="utf-8")) or {}).get("itens") or []
    except Exception:
        pass
    novos = [
        {**{k: v for k, v in p.items() if k not in ocultar}, "postado_em": agora.isoformat()}
        for p in postados
    ]
    ids_novos = {i["id"] for i in novos}
    itens = (novos + [i for i in antigos if i.get("id") not in ids_novos])[:TRENDS_MAX]
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    TRENDS.write_text(
        json.dumps({"atualizado": agora.isoformat(), "itens": itens}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat(), "(DRY_RUN)" if DRY_RUN else "")
    print(f"Meta: Shopee | {MAX_POSTS} posts/execução | bloqueio {REPETIR_APOS_DIAS}d | "
          f"min vendas {SHOPEE_MIN_VENDAS} | queda mín {QUEDA_MIN_PCT}%")

    if not pode_postar(agora) and not DRY_RUN:
        print(f"Fora do horário ({agora.hour}h) — nada a fazer")
        return

    if not DRY_RUN:
        fb_diagnostico()
    print(f"Canais: Telegram={'sim' if TG_TOKEN and TG_CHAT else 'não'} | Facebook={'sim' if FB_ATIVO else 'não'}")

    estado = limpar_estado(carregar_estado(), agora)
    print(f"Bloqueados: {len(estado['ids'])} ids | preços rastreados: {len(estado['precos'])}")

    pool = coletar_shopee(estado)
    escolhidos = escolher(pool, MAX_POSTS)
    quedas = sum(1 for p in escolhidos if p.get("preco_anterior"))
    print(f"Candidatos: {len(pool)} | selecionados: {len(escolhidos)} (quedas de preço: {quedas})")

    if DRY_RUN:
        for p in escolhidos:
            print(f"\n--- score {p['score']:.1f} | busca '{p['busca']}' ---")
            print("TELEGRAM:\n" + texto_post(p, False))
            print("\nFACEBOOK:\n" + texto_post(p, True))
        return

    if not escolhidos:
        print("Nada a postar")
        salvar(estado)
        enviar_alertas(estado, agora)
        return

    if not ((TG_TOKEN and TG_CHAT) or FB_ATIVO):
        add_alerta("sem_canal", "Nenhum canal ativo (Telegram/Facebook) — nada foi postado.")
        salvar(estado)
        enviar_alertas(estado, agora)
        return

    postados = []
    for i, p in enumerate(escolhidos):
        if postar(p):
            registrar(estado, p, agora)
            postados.append(p)
            salvar(estado)
        if i < len(escolhidos) - 1:
            time.sleep(POST_INTERVALO_SEG * random.uniform(0.7, 1.3))

    if postados:
        atualizar_trends(postados, agora)
    else:
        add_alerta("sem_envio", "Havia produtos selecionados, mas nenhum canal conseguiu postar.")

    salvar(estado)
    print("Postagens enviadas:", len(postados))
    enviar_alertas(estado, agora)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        enviar_privado(f"⚠️ <b>Radar Shopee</b>\n\n• Falha inesperada: {html.escape(type(e).__name__)}: "
                       f"{html.escape(str(e)[:200])}")
        raise
