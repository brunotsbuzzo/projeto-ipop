"""Tela de lista de regras: pesquisar, copiar o modelo e abrir a cópia (antiga BasePage)."""

from __future__ import annotations

import logging
import re

from playwright.sync_api import Locator, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


class ListaRegrasPage:
    # Editor lateral de regra. Fica POR CIMA da lista e dos botões Copiar/Detalhes
    # enquanto estiver aberto. O campo "Pesquisar por código" abre a regra nele.
    SELETOR_EDITOR_ABERTO = (
        "#menu_vertical_editar_regras.mostrarMenuVerticalEditarRegras"
    )
    # Mesmo botão que o formulário usa para fechar (CadastroDeRegraPage.fechar_regra_btn).
    SELETOR_FECHAR_EDITOR = (
        "button.btn.btn-outline-light.btn-sm:not(.position-absolute)"
    )

    def __init__(self, page: Page):
        self.page = page
        self.busca_nome: Locator = page.locator(
            'input[placeholder="Pesquisar"][aria-controls="regras_lista"]'
        )
        self.tabela_linhas: Locator = page.locator("#regras_lista tbody tr")
        self.processando: Locator = page.locator("#regras_lista_processing")
        self.nova_btn: Locator = page.get_by_role("button", name="Nova", exact=True)
        self.copiar_btn: Locator = page.locator(
            "button.btn.btn-primary:has-text('Copiar')"
        )
        self.confirmar_btn: Locator = page.locator("button:has-text('Confirmar')")
        self.detalhes_btn: Locator = page.locator("button", has_text="Detalhes")
        self.editar_btn: Locator = page.locator("button", has_text="Editar")
        self.editor_aberto: Locator = page.locator(self.SELETOR_EDITOR_ABERTO)
        self.fechar_editor_btn: Locator = page.locator(self.SELETOR_FECHAR_EDITOR)

    # ---------- editor lateral ----------
    def fechar_editor_se_aberto(self, timeout: int = 15_000) -> None:
        """Fecha o editor lateral se ele estiver aberto (não salva nada)."""
        if self.editor_aberto.count() == 0 or not self.editor_aberto.first.is_visible():
            return
        logger.info("Editor lateral de regra aberto por cima da lista; fechando.")
        botao = self.editor_aberto.first.locator(self.SELETOR_FECHAR_EDITOR)
        if botao.count() == 0:
            botao = self.fechar_editor_btn
        botao.first.click(timeout=timeout)
        expect(self.editor_aberto).to_be_hidden(timeout=timeout)
        self.esperar_tabela()

    # ---------- esperas ----------
    def esperar_tabela(self, timeout: int = 60_000) -> None:
        if self.processando.count() > 0:
            expect(self.processando).to_be_hidden(timeout=timeout)

    def esperar_carregamento(self, timeout: int = 60_000) -> None:
        indicadores = self.page.locator(
            "div.dataTables_processing:visible, .loading:visible, .spinner:visible, "
            ".processing:visible, .overlay:visible"
        )
        try:
            expect(indicadores).to_have_count(0, timeout=timeout)
        except AssertionError:
            logger.warning(
                "Indicador de carregamento ainda visível após %d ms; seguindo.", timeout
            )

    def esperar_lista_pronta(self, timeout: int = 60_000) -> None:
        expect(self.busca_nome).to_be_editable(timeout=timeout)
        self.esperar_carregamento(timeout)

    # ---------- busca ----------
    def pesquisar_por_id(self, id_regra: int) -> None:
        # Usa a busca geral, que FILTRA a lista. O campo "Pesquisar por código" não
        # filtra: abre a regra no editor lateral, que cobre a lista e o botão Copiar.
        self.pesquisar_por_nome(str(id_regra))

    def pesquisar_por_nome(self, nome: str) -> None:
        self.fechar_editor_se_aberto()
        expect(self.busca_nome).to_be_editable(timeout=10_000)
        self.busca_nome.fill(str(nome))
        self.busca_nome.press("Enter")
        self.esperar_tabela()

    def _clicar_linha(self, linha: Locator) -> None:
        """Seleciona a linha clicando na primeira célula (código).

        Se o editor lateral estiver aberto, ele cobre a lista e o clique cai nele.
        Nesse caso fecha o editor e tenta de novo. Nunca força o clique por baixo
        de outra tela (dispatch_event): melhor falhar do que selecionar errado.
        """
        self.fechar_editor_se_aberto()
        celula = linha.locator("td").first
        try:
            celula.click(timeout=10_000)
        except PlaywrightTimeoutError:
            logger.warning(
                "Clique na linha %s bloqueado; fechando o editor lateral e tentando de novo.",
                linha.get_attribute("id"),
            )
            self.fechar_editor_se_aberto()
            celula.click(timeout=10_000)

    def selecionar_por_id(self, id_regra: int, timeout: int = 15_000) -> None:
        # Só dentro da tabela de regras: o IPOP tem outros <tr>/<td> com o mesmo número
        # (ex.: painel lateral), e clicar neles seleciona outra coisa.
        linha = self.page.locator(f'#regras_lista tbody tr[id="{id_regra}"]')
        try:
            expect(linha).to_be_visible(timeout=timeout)
        except AssertionError:
            celula = (
                self.page.locator("#regras_lista tbody td")
                .filter(has_text=re.compile(rf"^\s*{id_regra}\s*$"))
                .first
            )
            expect(celula).to_be_visible(timeout=timeout)
            linha = celula.locator("xpath=ancestor::tr[1]")
        self._clicar_linha(linha)

    def selecionar_por_nome(self, nome: str, timeout: int = 15_000) -> None:
        celula = self.page.locator("#regras_lista tbody td").filter(has_text=nome).first
        expect(celula).to_be_visible(timeout=timeout)
        self._clicar_linha(celula.locator("xpath=ancestor::tr[1]"))

    def selecionar_primeira_linha(self, timeout: int = 15_000) -> None:
        expect(self.tabela_linhas.first).to_be_visible(timeout=timeout)
        self._clicar_linha(self.tabela_linhas.first)

    # ---------- leitura da lista ----------
    def mostrar_mais_linhas(self, quantidade: str = "100") -> None:
        """Aumenta as linhas por página (o padrão do IPOP é 10), se a lista tiver o seletor."""
        seletor = self.page.locator('select[name="regras_lista_length"]')
        if seletor.count() == 0:
            return
        seletor.first.select_option(quantidade)
        self.esperar_tabela()

    def nome_da_regra(self, id_regra: int, timeout: int = 15_000) -> str:
        """Nome (2ª coluna) da linha com exatamente esse ID na lista atual."""
        linha = self.page.locator(f'#regras_lista tbody tr[id="{id_regra}"]')
        expect(linha).to_be_visible(timeout=timeout)
        return linha.locator("td").nth(1).inner_text().strip()

    def ids_das_copias(self, nome_modelo: str, timeout: int = 15_000) -> set[str]:
        """Pesquisa o nome do modelo e devolve os IDs das linhas 'Cópia de ...' achadas."""
        self.pesquisar_por_nome(nome_modelo)
        # O próprio modelo sempre aparece nessa busca: espera a lista refletir a pesquisa.
        expect(self.tabela_linhas.first).to_contain_text(
            nome_modelo, ignore_case=True, timeout=timeout
        )
        linhas = self.page.eval_on_selector_all(
            "#regras_lista tbody tr",
            "rs => rs.map(r => [r.id || '', ((r.cells[1] || {}).innerText || '').trim()])",
        )
        return {
            id_
            for id_, nome in linhas
            if id_
            and nome.casefold().startswith("cópia de")
            and nome_modelo.casefold() in nome.casefold()
        }

    def regra_existe(self, nome: str, timeout: int = 15_000) -> bool:
        self.pesquisar_por_nome(nome)
        celula = self.page.locator("#regras_lista td").filter(
            has_text=re.compile(rf"^\s*{re.escape(nome)}\s*$")
        )
        try:
            expect(celula.first).to_be_visible(timeout=timeout)
            return True
        except AssertionError:
            return False

    # ---------- ações ----------
    def abrir_nova_regra(self) -> None:
        self.nova_btn.click()

    def copiar_selecionada(self) -> None:
        self.fechar_editor_se_aberto()
        # O IPOP só habilita o Copiar com uma linha selecionada.
        try:
            expect(self.copiar_btn.first).to_be_enabled(timeout=10_000)
        except AssertionError as erro:
            raise RuntimeError(
                "Botão Copiar desabilitado: nenhuma regra-modelo ficou selecionada na lista."
            ) from erro
        self.copiar_btn.first.click()
        self.confirmar_btn.click()
        expect(self.confirmar_btn.first).to_be_hidden(timeout=30_000)
        self.esperar_tabela()

    def abrir_selecionada(self) -> None:
        """Abre a regra selecionada. Subscrição usa 'Detalhes'; a Oferta antiga usava 'Editar'."""
        for botao in (self.detalhes_btn, self.editar_btn):
            if botao.count() > 0 and botao.first.is_visible():
                botao.first.click()
                return
        self.detalhes_btn.first.click(timeout=10_000)
