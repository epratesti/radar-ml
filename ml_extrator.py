"""Extrator de links de afiliado do Mercado Livre -> state/ml_lista.json

Abre o hub de afiliados com a sessão salva, clica em "Compartilhar" em cada produto,
lê o link meli.la e guarda título, preço, imagem e link do produto.

Uso:
  GitHub Actions : python ml_extrator.py            (usa o secret ML_COOKIES_JSON)
  Teste no PC    : python ml_extrator.py --headful  (usa o arquivo ml-session.json)

Variáveis de ambiente:
  ML_COOKIES_JSON   sessão (conteúdo do ml-session.json)
  ML_MAX_PRODUTOS   quantos produtos extrair por execução (padrão 25)
  ML_HUB_URL        URL do hub (padrão: hub de afiliados)
  ML_DEBUG          "true" salva prints/HTML em debug/

Códigos de saída: 0 ok | 2 sessão expirada | 3 nenhum botão Compartilhar | 4 nenhum link extraído
"""
import os, re, sys, json, time, random, argparse, datetime as dt
from pathlib import Path

ROOT = Path(__file__).parent
SAIDA = ROOT / "state" / "ml_lista.json"
DEBUG_DIR = ROOT / "debug"
HUB = os.getenv("ML_HUB_URL", "https://www.mercadolivre.com.br/afiliados/hub?is_affiliate=true")
MAX_PRODUTOS = int(os.getenv("ML_MAX_PRODUTOS", "25"))
DEBUG = os.getenv("ML_DEBUG", "").strip().lower() in ("1", "true", "yes")
MAX_NA_LISTA = 200

MELI_LA = re.compile(r"https?://meli\.la/[A-Za-z0-9_-]+")

# Sobe a partir do botão até o maior contêiner que tenha só UM botão "Compartilhar".
JS_CARTAO = """
(btn) => {
  const ehShare = (el) => el.tagName === 'BUTTON' && /compartilhar/i.test(el.innerText || '');
  let card = btn;
  for (let i = 0; i < 8 && card.parentElement; i++) {
    const p = card.parentElement;
    const n = Array.from(p.querySelectorAll('button')).filter(ehShare).length;
    if (n > 1) break;
    card = p;
  }
  const imgs = Array.from(card.querySelectorAll('img')).map(im => ({
    src: im.currentSrc || im.src || im.getAttribute('data-src') || '',
    alt: im.alt || '',
    area: (im.naturalWidth || 0) * (im.naturalHeight || 0)
  }));
  const links = Array.from(card.querySelectorAll('a[href]')).map(a => a.href);
  return { texto: card.innerText || '', imgs, links };
}
"""

JS_LER_LINK = """
() => {
  const re = /https?:\\/\\/meli\\.la\\/[A-Za-z0-9_-]+/g;
  const achados = [];
  document.querySelectorAll('input, textarea').forEach(el => {
    const m = (el.value || '').match(re); if (m) achados.push(...m);
  });
  const m2 = (document.body.innerText || '').match(re);
  if (m2) achados.push(...m2);
  return achados;
}
"""


class SessaoExpirada(Exception):
    pass


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def agora():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def carregar_sessao():
    raw = os.getenv("ML_COOKIES_JSON", "").strip()
    if not raw:
        f = ROOT / "ml-session.json"
        if f.exists():
            raw = f.read_text(encoding="utf-8")
    if not raw:
        return None
    data = json.loads(raw)
    if isinstance(data, list):
        data = {"cookies": data, "origins": []}
    return data


def parse_preco(texto):
    """Preço atual do cartão (melhor esforço). Ignora parcelas e valores de cashback/desconto."""
    t = (texto or "").replace("\xa0", " ")
    candidatos = []
    for m in re.finditer(r"R\$\s*([\d\.]+)(?:,(\d{2})|\n+(\d{2})(?=\n|$))?", t):
        antes = t[max(0, m.start() - 22):m.start()].lower()
        if re.search(r"\d\s*x\s*$", antes):          # parcelas: "10x R$ 26"
            continue
        if re.search(r"cashback|desconto|off|economi|comiss|ganh|cupom|frete", antes):
            continue
        inteiro = m.group(1).replace(".", "")
        cent = m.group(2) or m.group(3) or "00"
        try:
            v = float(f"{inteiro}.{cent}")
        except ValueError:
            continue
        if v > 0:
            candidatos.append(v)
    return min(candidatos) if candidatos else 0.0


IGNORAR_LINHA = re.compile(
    r"r\$|compartilhar|%|off\b|frete|ganhe|ganhos|comiss|cashback|vendido|avalia|"
    r"patrocin|oferta|cupom|parcel|sem juros|^\d+$", re.I)


def parse_titulo(texto, imgs):
    for im in imgs:
        alt = (im.get("alt") or "").strip()
        if len(alt) >= 15 and not IGNORAR_LINHA.search(alt):
            return alt[:150]
    for linha in (texto or "").split("\n"):
        linha = linha.strip()
        if len(linha) >= 15 and not IGNORAR_LINHA.search(linha):
            return linha[:150]
    return ""


def melhor_imagem(imgs):
    ok = [i for i in imgs if i.get("src", "").startswith("http") and not i["src"].endswith(".svg")]
    if not ok:
        return ""
    return max(ok, key=lambda i: i.get("area", 0))["src"]


def melhor_url_produto(links):
    prod = [u for u in links if re.search(r"mercadoli(vre|bre)\.com", u)]
    for u in prod:
        if re.search(r"MLB-?\d+|/p/MLB", u):
            return u.split("#")[0].split("?")[0]
    return prod[0].split("#")[0].split("?")[0] if prod else ""


def codigo_do_link(link):
    return link.rstrip("/").split("/")[-1]


def carregar_lista():
    try:
        d = json.loads(SAIDA.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else d.get("itens", [])
    except Exception:
        return []


def salvar_lista(itens):
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    itens = sorted(itens, key=lambda x: x.get("coletado_em", ""), reverse=True)[:MAX_NA_LISTA]
    SAIDA.write_text(json.dumps(itens, ensure_ascii=False, indent=2), encoding="utf-8")


def debug_print(page, nome):
    if not DEBUG:
        return
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"{nome}.png"), full_page=False)
        (DEBUG_DIR / f"{nome}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        print("  (debug) falha ao salvar:", e)


def ler_link(page, ja_coletados):
    """1) procura meli.la no DOM do pop-up; 2) fallback: botão copiar + área de transferência."""
    fim = time.time() + 6
    while time.time() < fim:
        try:
            for l in page.evaluate(JS_LER_LINK):
                if l not in ja_coletados:
                    return l
        except Exception:
            pass
        page.wait_for_timeout(300)
    try:
        page.locator("#copy_link, button[id='copy_link']").first.click(timeout=3000)
        page.wait_for_timeout(600)
        txt = page.evaluate("navigator.clipboard.readText()")
        m = MELI_LA.search(txt or "")
        if m and m.group(0) not in ja_coletados:
            return m.group(0)
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------------
# Principal
# ----------------------------------------------------------------------------
def extrair(headful=False, maximo=MAX_PRODUTOS):
    from playwright.sync_api import sync_playwright

    sessao = carregar_sessao()
    if not sessao:
        print("ERRO: sem sessão (defina ML_COOKIES_JSON ou crie ml-session.json com capturar_sessao.py)")
        return 2

    existentes = {i["link"]: i for i in carregar_lista() if i.get("link")}
    novos, ja_coletados = 0, set()
    debug_cartoes = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headful,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            storage_state=sessao,
            viewport={"width": 1400, "height": 900},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"],
                                  origin="https://www.mercadolivre.com.br")
        except Exception:
            pass
        page = ctx.new_page()

        try:
            print("Abrindo hub de afiliados...")
            page.goto(HUB, timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(4000)

            url = page.url.lower()
            if "login" in url or "/auth" in url or "registration" in url:
                debug_print(page, "sessao_expirada")
                raise SessaoExpirada(url)

            for _ in range(6):
                page.evaluate("window.scrollBy(0, 900)")
                page.wait_for_timeout(1100)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1000)
            debug_print(page, "hub")

            botoes = page.locator("button:has-text('Compartilhar')")
            total = botoes.count()
            print(f"Botões 'Compartilhar' encontrados: {total}")
            if total == 0:
                debug_print(page, "sem_botoes")
                return 3

            falhas_print = 0
            for i in range(min(total, maximo)):
                try:
                    btn = page.locator("button:has-text('Compartilhar')").nth(i)
                    if not btn.is_visible():
                        continue
                    btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(400)

                    cartao = btn.evaluate(JS_CARTAO)
                    titulo = parse_titulo(cartao["texto"], cartao["imgs"])
                    preco = parse_preco(cartao["texto"])
                    imagem = melhor_imagem(cartao["imgs"])
                    url_produto = melhor_url_produto(cartao["links"])
                    if DEBUG:
                        debug_cartoes.append({"i": i, "titulo": titulo, "preco": preco,
                                              "imagem": imagem, "url": url_produto,
                                              "texto": cartao["texto"][:600]})

                    btn.click(timeout=8000)
                    page.wait_for_timeout(1400)
                    link = ler_link(page, ja_coletados)
                    if not link and falhas_print < 2:
                        debug_print(page, f"modal_falhou_{i}")
                        falhas_print += 1
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)

                    rotulo = (titulo or f"produto {i+1}")[:55]
                    if not link:
                        print(f"[{i+1}] sem link: {rotulo}")
                        continue
                    ja_coletados.add(link)
                    item = {
                        "id": codigo_do_link(link),
                        "titulo": titulo,
                        "preco": preco,
                        "imagem": imagem,
                        "url_produto": url_produto,
                        "link": link,
                        "coletado_em": agora(),
                    }
                    if link in existentes:
                        # mantém dados anteriores se os novos vierem vazios
                        antigo = existentes[link]
                        for k in ("titulo", "imagem", "url_produto"):
                            if not item[k]:
                                item[k] = antigo.get(k, "")
                        if not item["preco"]:
                            item["preco"] = antigo.get("preco", 0.0)
                    else:
                        novos += 1
                    existentes[link] = item
                    print(f"[{i+1}] ok R$ {preco:.2f} | {rotulo} | {link}")
                    time.sleep(random.uniform(1.0, 2.2))
                except Exception as e:
                    print(f"[{i+1}] erro: {str(e)[:120]}")
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
        except SessaoExpirada as e:
            print(f"SESSÃO EXPIRADA (redirecionou para {e}). Gere novos cookies com capturar_sessao.py.")
            return 2
        finally:
            if DEBUG and debug_cartoes:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / "cartoes.json").write_text(
                    json.dumps(debug_cartoes, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()

    if not ja_coletados:
        print("Nenhum link extraído.")
        return 4

    salvar_lista(list(existentes.values()))
    print(f"Concluído: {len(ja_coletados)} links lidos, {novos} novos. Lista: {SAIDA}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headful", action="store_true", help="abre o navegador visível (teste no PC)")
    ap.add_argument("--max", type=int, default=MAX_PRODUTOS)
    args = ap.parse_args()
    sys.exit(extrair(headful=args.headful, maximo=args.max))
