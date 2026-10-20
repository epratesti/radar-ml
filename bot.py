"""Radar: 4 produtos diversificados por hora → Telegram.
Evita repetições por 14 dias.
Horário (America/Sao_Paulo):
  - 07h–22h: a cada 1 hora
  - 23h, 02h e 05h: a cada 3 horas (madrugada)
"""
import os, json, html, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
AFF_PARAMS = os.getenv("ML_AFFILIATE_PARAMS", "matt_tool={id}")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
MAX_POSTS = int(os.getenv("MAX_POSTS", "4"))
REPETIR_APOS_DIAS = 14  # evita repetir o mesmo produto por 2 semanas
API = "https://api.mercadolibre.com"
ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")

# Grupos para diversificar (1 produto de cada grupo por hora)
GRUPOS = [
    {"nome": "Eletrônicos", "cats": ["MLB1000", "MLB1051", "MLB1648"]},      # eletrônicos / celulares / informática
    {"nome": "Casa", "cats": ["MLB1574", "MLB5726"]},                        # casa / eletrodomésticos
    {"nome": "Moda", "cats": ["MLB1430", "MLB3937"]},                        # roupas / relógios
    {"nome": "Esportes e Beleza", "cats": ["MLB1276", "MLB1246", "MLB1132"]}, # fitness / beleza / brinquedos
]

CAT_NOMES = {
    "MLB1051": "Celulares", "MLB1430": "Moda", "MLB1144": "Games",
    "MLB1000": "Eletrônicos", "MLB1276": "Esportes", "MLB1648": "Informática",
    "MLB1246": "Beleza", "MLB5726": "Eletrodomésticos", "MLB1132": "Brinquedos",
    "MLB1574": "Casa", "MLB3937": "Relógios",
}

S = requests.Session()
S.headers.update({"User-Agent": "radar-ml/1.3", "Accept": "application/json"})


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


def montar_produto(pid, tipo, h, categoria):
    titulo, foto = "", ""
    preco, frete = 0.0, False

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
            results = ri.json().get("results") or []
            if results:
                best = min(results, key=lambda x: x.get("price") or 999999)
                preco = float(best.get("price") or 0)
                frete = (best.get("shipping") or {}).get("free_shipping", False)
    except Exception as e:
        print(f"  items/{pid}:", e)

    if not titulo or preco <= 0:
        return None

    return {
        "id": pid,
        "titulo": titulo,
        "preco": preco,
        "imagem": foto,
        "frete_gratis": frete,
        "categoria": categoria,
        "link": link_afiliado(f"https://www.mercadolivre.com.br/p/{pid}"),
    }


def um_da_categoria(cat_id, h, vistos):
    nome = CAT_NOMES.get(cat_id, cat_id)
    try:
        r = S.get(f"{API}/highlights/MLB/category/{cat_id}", headers=h, timeout=20)
        if r.status_code != 200:
            print(f"Highlights {nome}: {r.status_code}")
            return None
        for item in r.json().get("content", [])[:12]:  # olha um pouco mais de candidatos
            pid = item.get("id")
            if not pid or pid in vistos:
                continue
            p = montar_produto(pid, item.get("type", "PRODUCT"), h, nome)
            if p:
                print(f"  OK [{nome}] R$ {p['preco']:.2f} — {p['titulo'][:50]}")
                return p
    except Exception as e:
        print(f"Erro {nome}:", e)
    return None


def montar_diversificado(h, estado):
    """1 produto de cada grupo (4 no total), evitando repetidos."""
    vistos = set(estado.keys())
    escolhidos = []
    for grupo in GRUPOS:
        p = None
        for cat_id in grupo["cats"]:
            p = um_da_categoria(cat_id, h, vistos)
            if p:
                break
        if p:
            vistos.add(p["id"])
            escolhidos.append(p)
        if len(escolhidos) >= MAX_POSTS:
            break
    return escolhidos


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
    """
    07h–22h → toda hora
    23h, 02h e 05h → madrugada (a cada 3 horas)
    """
    h = agora.hour
    if 7 <= h <= 22:
        return True
    if h in (23, 2, 5):
        return True
    return False


def main():
    agora = dt.datetime.now(TZ)
    print("Iniciando", agora.isoformat())
    h = headers_ml()
    if not h:
        return

    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = STATE.read_text(encoding="utf-8").strip() or "{}"
        estado = json.loads(raw)
        if not isinstance(estado, dict):
            estado = {}
    except Exception as e:
        print("Aviso: estado inválido, reiniciando:", e)
        estado = {}

    # Remove produtos antigos demais (fora da janela de anti-repetição)
    limite = agora - dt.timedelta(days=REPETIR_APOS_DIAS)
    estado_limpo = {}
    for k, v in estado.items():
        try:
            if dt.datetime.fromisoformat(v) > limite:
                estado_limpo[k] = v
        except Exception:
            continue
    estado = estado_limpo
    print(f"Produtos já postados (últimos {REPETIR_APOS_DIAS} dias):", len(estado))

    ranking = montar_diversificado(h, estado)
    print("Produtos selecionados:", len(ranking))

    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    TRENDS.write_text(json.dumps({"atualizado": agora.isoformat(), "itens": ranking},
                                 ensure_ascii=False, indent=2), encoding="utf-8")

    if not ranking:
        print("Nada a postar")
        # ainda grava o estado limpo
        STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not (TG_TOKEN and TG_CHAT):
        print("Falta Telegram")
        return
    if not pode_postar_agora(agora):
        print(f"Fora do horário de postagem (hora atual: {agora.hour}h). "
              "Dia: 7h–22h | Madrugada: 23h, 2h e 5h")
        STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    enviados = 0
    for p in ranking:
        if enviados >= MAX_POSTS:
            break
        # dupla checagem por segurança
        if p["id"] in estado:
            print(f"  Já postado recentemente, pulando: {p['id']}")
            continue
        if postar(p):
            estado[p["id"]] = agora.isoformat()
            enviados += 1

    STATE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Postagens enviadas:", enviados)


if __name__ == "__main__":
    main()
