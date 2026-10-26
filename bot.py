"""Radar ML → Telegram. Busca ML (sem auth se 403) + highlights fallback."""
import os, json, html, re, random, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
AFF_PARAMS = os.getenv("ML_AFFILIATE_PARAMS", "matt_tool={id}")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
POSTS_POR_GRUPO = int(os.getenv("POSTS_POR_GRUPO", "2"))
MAX_POSTS = int(os.getenv("MAX_POSTS", "8"))
REPETIR_APOS_DIAS = int(os.getenv("REPETIR_APOS_DIAS", "3"))
API = "https://api.mercadolibre.com"
ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

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

CAT_NOMES = {
    "MLB1000": "Eletrônicos", "MLB1051": "Celulares", "MLB1648": "Informática",
    "MLB1574": "Casa", "MLB5726": "Eletrodomésticos", "MLB1430": "Moda",
    "MLB3937": "Relógios", "MLB1276": "Esportes", "MLB1246": "Beleza", "MLB1132": "Brinquedos",
}

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/2.5", "Accept": "application/json"})


def headers_ml():
    cid, sec = os.getenv("ML_CLIENT_ID"), os.getenv("ML_CLIENT_SECRET")
    if not (cid and sec):
        print("ERRO: secrets ML ausentes"); return {}
    r = S.post(f"{API}/oauth/token", data={
        "grant_type": "client_credentials", "client_id": cid, "client_secret": sec
    }, timeout=20)
    r.raise_for_status()
    print("Token ML OK")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def link_afiliado(url):
    sep = "&" if "?" in url else "?"
    return url + sep + AFF_PARAMS.format(id=AFF_ID)


def norm(t):
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9áéíóúâêôãõç\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:80]


def carregar_estado():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(STATE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            return {"ids": {}, "titulos": {}}
        if data and "ids" not in data:
            return {"ids": data, "titulos": {}}
        data.setdefault("ids", {}); data.setdefault("titulos", {})
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
    return {
        "id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
        "frete_gratis": frete, "categoria": label,
        "link": link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}"),
        "item_id": mid,
    }


def ml_get(url, h, params=None):
    """GET com token; se 403, tenta de novo sem Authorization (endpoint público)."""
    r = S.get(url, headers=h or {}, params=params, timeout=25)
    if r.status_code == 403 and h:
        r = S.get(url, headers={"Accept": "application/json", "User-Agent": "radar-ml/2.5"},
                  params=params, timeout=25)
    return r


def buscar(query, h, vistos, vistos_t, label, n):
    achados = []
    try:
        offset = random.choice([0, 0, 10, 20, 30, 40])
        r = ml_get(f"{API}/sites/MLB/search", h, params={
            "q": query, "limit": 50, "offset": offset,
            "sort": random.choice(["relevance", "sold_quantity_desc"]),
        })
        if r.status_code != 200:
            print(f"  search {query}: {r.status_code}")
            return achados
        results = list(r.json().get("results") or [])
        random.shuffle(results)
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
            vistos.add(p["id"])
            if p.get("item_id"):
                vistos.add(str(p["item_id"]))
            if nt:
                vistos_t.add(nt)
            achados.append(p)
            print(f"  OK [{label}] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
    except Exception as e:
        print(f"Erro {query}:", e)
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
            pid = str(item.get("id") or "")
            if not pid or pid in vistos:
                continue
            # product detail may need token
            titulo, preco, foto, frete, mid = "", 0.0, "", False, None
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
            except Exception as e:
                print(f"  product {pid}:", e)
                continue
            if not titulo or preco <= 0:
                continue
            nt = norm(titulo)
            if nt and nt in vistos_t:
                continue
            if mid:
                m = str(mid)
                mid_fmt = (m[:3] + "-" + m[3:]) if m.startswith("MLB") and "-" not in m else m
                link = link_afiliado(f"https://produto.mercadolivre.com.br/{mid_fmt}")
            else:
                link = link_afiliado(f"https://www.mercadolivre.com.br/p/{pid}")
            p = {"id": pid, "titulo": titulo, "preco": preco, "imagem": foto,
                 "frete_gratis": frete, "categoria": nome, "link": link, "item_id": mid}
            vistos.add(pid)
            if mid:
                vistos.add(str(mid))
            if nt:
                vistos_t.add(nt)
            achados.append(p)
            print(f"  OK [{nome}/hl] R$ {preco:.2f} — {titulo[:50]}")
    except Exception as e:
        print(f"Erro highlights {nome}:", e)
    return achados


def montar(h, estado):
    vistos = set(str(k) for k in estado["ids"])
    vistos_t = set(estado["titulos"])
    out = []
    grupos = list(GRUPOS)
    random.shuffle(grupos)
    for g in grupos:
        if len(out) >= MAX_POSTS:
            break
        qs = list(g["queries"]); random.shuffle(qs)
        got = 0
        print(f"Grupo {g['nome']}: até {POSTS_POR_GRUPO}")
        for q in qs:
            if got >= POSTS_POR_GRUPO or len(out) >= MAX_POSTS:
                break
            falta = min(POSTS_POR_GRUPO - got, MAX_POSTS - len(out))
            novos = buscar(q, h, vistos, vistos_t, g["nome"], falta)
            out.extend(novos)
            got += len(novos)
        # fallback highlights se busca falhou
        if got < POSTS_POR_GRUPO:
            for cat in g["cats"]:
                if got >= POSTS_POR_GRUPO or len(out) >= MAX_POSTS:
                    break
                falta = min(POSTS_POR_GRUPO - got, MAX_POSTS - len(out))
                novos = buscar_highlights(cat, h, vistos, vistos_t, falta)
                out.extend(novos)
                got += len(novos)
    return out[:MAX_POSTS]


def preco_br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def postar(p):
    cat = html.escape(p.get("categoria", ""))
    titulo = html.escape(p["titulo"][:120])
    preco = preco_br(p["preco"])
    link = html.escape(p["link"])
    frete = "🚚 <b>Frete grátis</b>\n" if p.get("frete_gratis") else ""
    texto = (
        f"🔥 <b>Achado do momento — {cat}</b>\n\n"
        f"📦 {titulo}\n\n"
        f"💰 <b>{preco}</b>\n{frete}"
        f"\n👉 <a href=\"{link}\">Ver oferta no Mercado Livre</a>\n\n"
        f"<i>Link de afiliado · Preços podem variar</i>"
    )
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram não configurado"); return False
    data = {"chat_id": TG_CHAT, "parse_mode": "HTML"}
    if p.get("imagem"):
        data["photo"] = p["imagem"]; data["caption"] = texto
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", data=data, timeout=30)
    else:
        data["text"] = texto
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=30)
    print("Telegram:", r.status_code, r.text[:120])
    return r.ok


def pode_postar(agora):
    return 7 <= agora.hour <= 22


def salvar(estado):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    print(f"Meta: {POSTS_POR_GRUPO}/grupo máx {MAX_POSTS} | bloqueio {REPETIR_APOS_DIAS}d")
    h = headers_ml()
    if not h:
        return
    estado = limpar_estado(carregar_estado(), agora)
    print(f"Bloqueados: {len(estado['ids'])} ids")
    ranking = montar(h, estado)
    ranking = [p for p in ranking if not ja_postado(estado, p["id"], p["titulo"], p.get("item_id"))]
    print("Selecionados:", len(ranking))
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    itens = [{k: v for k, v in p.items() if k != "item_id"} for p in ranking]
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": itens},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    if not ranking:
        print("Nada a postar"); salvar(estado); return
    if not (TG_TOKEN and TG_CHAT):
        print("Falta Telegram"); return
    if not pode_postar(agora):
        print(f"Fora do horário ({agora.hour}h)"); salvar(estado); return
    enviados = 0
    for p in ranking:
        if enviados >= MAX_POSTS:
            break
        if ja_postado(estado, p["id"], p["titulo"], p.get("item_id")):
            continue
        if postar(p):
            registrar(estado, p["id"], p["titulo"], agora, p.get("item_id"))
            enviados += 1
            salvar(estado)
    salvar(estado)
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
