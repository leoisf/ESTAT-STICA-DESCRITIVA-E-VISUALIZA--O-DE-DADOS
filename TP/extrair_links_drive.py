"""Extrai destinos do Google Drive das referências do Moodle em Link_aulas.txt.

Uso:
    python extrair_links_drive.py

Dependência:
    python -m pip install selenium

O navegador é aberto de forma visível para permitir a autenticação manual. O
script não solicita, lê nem armazena usuário ou senha.
"""

from __future__ import annotations

import re
import sys
import time
import winreg
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    print("A biblioteca Selenium não está instalada.")
    print("Execute: python -m pip install selenium")
    raise SystemExit(1)


ARQUIVO = Path(__file__).with_name("Link_aulas.txt")
MOODLE_RE = re.compile(
    r"^\s*(https://www\.salasvirtuais\.ufop\.br/mod/(?:url/view|forum/discuss)\.php\?\S+)\s*$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def navegador_padrao_windows() -> str:
    """Retorna edge, chrome ou firefox a partir da associação HTTP do Windows."""
    chave = (
        r"Software\Microsoft\Windows\Shell\Associations"
        r"\UrlAssociations\https\UserChoice"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as registro:
            prog_id = str(winreg.QueryValueEx(registro, "ProgId")[0]).lower()
    except OSError:
        return "edge"

    if "chrome" in prog_id:
        return "chrome"
    if "firefox" in prog_id:
        return "firefox"
    if "edge" in prog_id or "msedge" in prog_id:
        return "edge"
    return "edge"


def abrir_navegador(nome: str):
    """Abre um navegador controlado pelo Selenium e visível ao usuário."""
    if nome == "chrome":
        opcoes = webdriver.ChromeOptions()
        opcoes.add_argument("--start-maximized")
        return webdriver.Chrome(options=opcoes)
    if nome == "firefox":
        return webdriver.Firefox()

    opcoes = webdriver.EdgeOptions()
    opcoes.add_argument("--start-maximized")
    return webdriver.Edge(options=opcoes)


def normalizar_drive(url: str) -> str | None:
    """Valida e desembrulha URLs do Google que apontem para Drive/Docs."""
    url = (
        url.replace(r"\u003d", "=")
        .replace(r"\u0026", "&")
        .replace("&amp;", "&")
        .strip()
        .rstrip(".,;)")
    )
    try:
        partes = urlparse(url)
    except ValueError:
        return None

    host = partes.netloc.lower().split(":", 1)[0]
    if host in {"drive.google.com", "docs.google.com"}:
        return url

    # Alguns links Google/Moodle carregam o destino em url=, q= ou continue=.
    consulta = parse_qs(partes.query)
    for campo in ("url", "q", "continue"):
        for valor in consulta.get(campo, []):
            destino = normalizar_drive(unquote(valor))
            if destino:
                return destino
    return None


def chave_drive(url: str) -> str:
    """Agrupa variações de URL que apontam para o mesmo recurso do Drive."""
    padroes = (
        r"/(?:file/d|document/d|spreadsheets/d|presentation/d|folders)/([^/?#&]+)",
        r"[?&]id=([^&#]+)",
    )
    for padrao in padroes:
        achado = re.search(padrao, url, re.IGNORECASE)
        if achado:
            return achado.group(1)
    partes = urlparse(url)
    return f"{partes.netloc.lower()}{partes.path.rstrip('/')}"


def candidatos_da_pagina(driver) -> list[str]:
    """Coleta destinos no URL atual, links, iframes e HTML da página."""
    encontrados: list[str] = []

    def adicionar(valor: str | None) -> None:
        if not valor:
            return
        destino = normalizar_drive(valor)
        if destino and destino not in encontrados:
            encontrados.append(destino)

    adicionar(driver.current_url)

    for seletor, atributo in (("a[href]", "href"), ("iframe[src]", "src")):
        for elemento in driver.find_elements(By.CSS_SELECTOR, seletor):
            adicionar(elemento.get_attribute(atributo))

    for url in URL_RE.findall(driver.page_source):
        adicionar(url.replace("&amp;", "&"))

    # O Google costuma expor a mesma mídia como URL limpa, URL com escapes JSON
    # e URL de autenticação. Mantém uma só variante por ID, priorizando a menor.
    unicos: dict[str, str] = {}
    for url in encontrados:
        chave = chave_drive(url)
        atual = unicos.get(chave)
        if atual is None or len(url) < len(atual):
            unicos[chave] = url
    return list(unicos.values())


def escolher_candidato(origem: str, candidatos: list[str]) -> str | None:
    if not candidatos:
        print("  Nenhum link do Drive encontrado.")
        return None
    if len(candidatos) == 1:
        print(f"  Drive: {candidatos[0]}")
        return candidatos[0]

    print("  Foram encontrados vários recursos distintos do Drive:")
    for indice, candidato in enumerate(candidatos, start=1):
        print(f"    {indice}. {candidato}")
    print("  Usando automaticamente o primeiro link exibido.")
    return candidatos[0]


def extrair_destino(driver, origem: str) -> str | None:
    print(f"Acessando: {origem}")
    driver.get(origem)

    # Aguarda o redirecionamento do recurso. Em fóruns, a própria página será
    # analisada depois do carregamento.
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass
    time.sleep(2)

    return escolher_candidato(origem, candidatos_da_pagina(driver))


def atualizar_arquivo(linhas: list[str], destinos: dict[str, str]) -> int:
    """Insere ou substitui o link Drive imediatamente após cada referência."""
    saida: list[str] = []
    alteracoes = 0
    indice = 0

    while indice < len(linhas):
        linha = linhas[indice]
        saida.append(linha)
        correspondencia = MOODLE_RE.match(linha)

        if correspondencia:
            origem = correspondencia.group(1)
            destino = destinos.get(origem)
            proximo_e_drive = (
                indice + 1 < len(linhas)
                and normalizar_drive(linhas[indice + 1].strip()) is not None
            )

            if destino:
                nova_linha = f"\t{destino}"
                if proximo_e_drive:
                    if linhas[indice + 1].strip() != destino:
                        # Acrescenta o novo destino sem apagar o link que já
                        # existia no arquivo original.
                        saida.append(nova_linha)
                        alteracoes += 1
                    else:
                        saida.append(linhas[indice + 1])
                        indice += 1
                else:
                    saida.append(nova_linha)
                    alteracoes += 1

        indice += 1

    if alteracoes:
        ARQUIVO.write_text("\n".join(saida) + "\n", encoding="utf-8")
    return alteracoes


def main() -> int:
    if sys.platform != "win32":
        print("Este script foi preparado para identificar o navegador padrão do Windows.")
        return 1
    if not ARQUIVO.exists():
        print(f"Arquivo não encontrado: {ARQUIVO}")
        return 1

    texto = ARQUIVO.read_text(encoding="utf-8-sig")
    linhas = texto.splitlines()
    referencias = [m.group(1) for linha in linhas if (m := MOODLE_RE.match(linha))]
    if not referencias:
        print("Nenhuma referência compatível do Moodle foi encontrada.")
        return 1

    nome = navegador_padrao_windows()
    print(f"Navegador padrão identificado: {nome}")
    print(f"Referências encontradas: {len(referencias)}")

    try:
        driver = abrir_navegador(nome)
    except WebDriverException as erro:
        print(f"Não foi possível abrir o navegador: {erro}")
        print("Atualize o navegador e tente novamente.")
        return 1

    destinos: dict[str, str] = {}
    try:
        driver.get("https://www.salasvirtuais.ufop.br/")
        print("\nFaça a autenticação no Moodle na janela aberta.", flush=True)
        print(
            "O processamento começará automaticamente após o login "
            "(tempo máximo: 10 minutos).",
            flush=True,
        )

        def autenticado(d) -> bool:
            url = d.current_url.lower()
            if "/login/" in url:
                return False
            # Em páginas autenticadas do Moodle existe normalmente um link de
            # logout ou o menu do usuário. A URL fora de /login/ é mantida como
            # alternativa para instalações que personalizam esses elementos.
            possui_logout = bool(
                d.find_elements(By.CSS_SELECTOR, "a[href*='login/logout.php']")
            )
            possui_menu_usuario = bool(
                d.find_elements(By.CSS_SELECTOR, "[data-region='user-menu']")
            )
            return possui_logout or possui_menu_usuario or "my/" in url

        try:
            WebDriverWait(driver, 600, poll_frequency=1).until(autenticado)
        except Exception:
            print("\nA autenticação não foi detectada em 10 minutos.", flush=True)
            return 1

        print("Autenticação detectada. Iniciando a extração...", flush=True)

        for numero, origem in enumerate(referencias, start=1):
            print(f"\n[{numero}/{len(referencias)}]", flush=True)
            try:
                destino = extrair_destino(driver, origem)
            except WebDriverException as erro:
                print(f"  Falha ao acessar a referência: {erro.msg}")
                destino = None
            if destino:
                destinos[origem] = destino
    finally:
        driver.quit()

    alteracoes = atualizar_arquivo(linhas, destinos)
    print(f"\nLinks identificados: {len(destinos)} de {len(referencias)}")
    print(f"Linhas acrescentadas ou atualizadas: {alteracoes}")
    if alteracoes:
        print(f"Arquivo atualizado: {ARQUIVO}")
    else:
        print("O arquivo não precisou ser alterado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
