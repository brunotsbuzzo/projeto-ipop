from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime
from time import perf_counter
from typing import Any, Callable, Iterable

from playwright.sync_api import Locator, Page, Route, expect

from ipop.models.regra import AcaoVariavel, RegraBase

logger = logging.getLogger(__name__)


@contextmanager
def medir_tempo(etapa: str):
    inicio = perf_counter()

    try:
        yield
    finally:
        logger.info(
            "%s executado em %.2fs",
            etapa,
            perf_counter() - inicio,
        )

class CadastroDeRegraPage:
    """Page Object da tela de cadastro de regras."""

    GRID_API_PATTERN = "**/RecuperaGridIndicadores"
    # 193 = CPF / CNPJ (agrupamento). Inclua outros IDs aqui se ficarem pesados.
    VARIAVEIS_GRID_PESADO = {193}

    # Parâmetros extras enviados na requisição do grid para o servidor já
    # devolver só o necessário. Deixe vazio para apenas filtrar a resposta.
    # Ex.: {"NomeDoParametro": "valor"}  (pegue o nome no DevTools > Network)
    PARAMETROS_EXTRAS_REQUISICAO_GRID: dict[str, Any] = {}

    TIMEOUT_FETCH_GRID_MS = 180_000
    TIMEOUT_ESPERA_GRID_MS = 200_000  # precisa ser maior que o fetch

    def __init__(self, page: Page):
        self.page = page

        # Seção Regra
        self.nome_regra_input: Locator = page.locator("#nome_regra_input")
        self.data_inicial_input: Locator = page.locator("#data_inicial_input")
        self.hora_inicial_input: Locator = page.locator("#hora_inicial_input")
        self.data_final_input: Locator = page.locator("#data_final_input")
        self.hora_final_input: Locator = page.locator("#hora_final_input")
        self.ajuste_input: Locator = page.locator("#percentual_ajuste_input")
        self.desconto_input: Locator = page.locator("#percentual_desconto_input")
        self.tipo_regra_select: Locator = page.locator("#tipo_select")
        self.subs_balanceada_select: Locator = page.locator("#subscricao_balanceada_select")
        self.categoria_select: Locator = page.locator("#categoria_select")
        self.mensagem_hdi_digital_input: Locator = page.locator("#mensagem_hdi_digital_textarea")
        self.mensagem_subs_balanceada_input: Locator = page.locator(
            "#subscricao_balanceada_hdi_digital_textarea"
        )
        self.comentario_input: Locator = page.locator("#comentario_textarea")
        self.salvar_btn: Locator = page.get_by_role("button", name="Salvar")
        self.excluir_btn: Locator = page.get_by_role("button", name="Excluir")
        self.retificar_btn: Locator = page.get_by_role("button", name="Retificar")
        self.aprovar_btn: Locator = page.get_by_role("button", name="Aprovação")
        self.encerrar_btn: Locator = page.get_by_role("button", name="Encerrar")

        # Seção Variáveis
        self.grupo_variavel_select: Locator = page.locator("#grupo_variaveis_select")
        self.variavel_select: Locator = page.locator("select[name='variavel_select']")
        self.tipo_select: Locator = page.locator("#tipo_lista_select")
        self.select_dominio: Locator = page.locator("#dominio_select")

        # Seção Indicadores
        self.valor_inicial_input: Locator = page.locator("#valor_inicial_input")
        self.valor_final_input: Locator = page.locator("#valor_final_input")
        self.pesquisar_input: Locator = page.get_by_placeholder("Pesquisar")
        self.selecionar_todas_linhas_btn: Locator = page.get_by_title(
            "Selecionar todas as linhas visíveis"
        )

        # Seção Árvore
        self.igual_btn: Locator = page.locator("#igualdade_button")
        self.diferente_btn: Locator = page.locator("#exclusao_button")
        self.remover_btn: Locator = page.locator("#remover_button")

        # Fechar página
        self.fechar_regra_btn: Locator = page.locator(
            "button.btn.btn-outline-light.btn-sm:not(.position-absolute)"
        )

        # Estado da interceptação
        self._grid_handler = None
        self._grid_filtro_concluido = False
        self._grid_filtro_erro: Exception | None = None
        self._grid_respostas_intermediarias = 0
        self._grid_quantidade_original: int | None = None
        self._grid_quantidade_filtrada: int | None = None

    def limpar_e_digitar(self, locator: Locator, valor: str, delay: int = 200) -> None:
        locator.click(position={"x": 0, "y": 10})
        locator.fill("")
        locator.type(valor, delay=delay)

    def _aguardar_opcao(
        self,
        select: Locator,
        valor: Any,
        *,
        timeout: int = 20_000,
    ) -> Locator:
        """Aguarda uma opção dinâmica existir e estar habilitada."""
        opcao = select.locator(f"option[value={str(valor)!r}]")
        expect(opcao).to_be_attached(timeout=timeout)
        expect(opcao).to_be_enabled(timeout=timeout)
        return opcao

    def _selecionar_opcao(
        self,
        select: Locator,
        valor: Any,
        *,
        timeout: int = 20_000,
        aguardar_depois: Callable[[], None] | None = None,
    ) -> None:
        """Seleciona e valida o valor, aguardando opcionalmente o próximo estado."""
        valor_texto = str(valor)
        expect(select).to_be_visible(timeout=timeout)
        expect(select).to_be_enabled(timeout=timeout)
        self._aguardar_opcao(select, valor_texto, timeout=timeout)
        select.select_option(value=valor_texto, timeout=timeout)
        expect(select).to_have_value(valor_texto, timeout=timeout)
        if aguardar_depois is not None:
            aguardar_depois()

    @staticmethod
    def _visivel(locator: Locator) -> bool:
        return locator.count() > 0 and locator.first.is_visible()

    @staticmethod
    def _texto_percentual(valor: float) -> str:
        return str(int(valor)) if float(valor).is_integer() else str(valor)

    def preencher_secao_regra(self, regra: RegraBase) -> None:
        expect(self.nome_regra_input).to_be_editable(timeout=30_000)
        self.nome_regra_input.fill("")
        self.nome_regra_input.fill(regra.nome_regra)
        self._preencher_data(self.data_inicial_input, regra.data_inicial)
        self._preencher_data(self.data_final_input, regra.data_final)
        if self._visivel(self.hora_inicial_input):
            self.hora_inicial_input.fill(regra.hora_inicial)
        if self._visivel(self.hora_final_input):
            self.hora_final_input.fill(regra.hora_final)

        # Subscrição usa #percentual_ajuste_input; Oferta usa #percentual_desconto_input
        # (é o campo que o robô Selenium de Oferta preenchia).
        campo_percentual = self.ajuste_input
        if regra.eh_oferta and self._visivel(self.desconto_input):
            campo_percentual = self.desconto_input
        elif not self._visivel(self.ajuste_input) and self._visivel(self.desconto_input):
            campo_percentual = self.desconto_input
        campo_percentual.fill(self._texto_percentual(regra.ajuste))
        expect(campo_percentual).not_to_have_value("", timeout=5_000)

        if not regra.eh_oferta:
            self.tipo_regra_select.select_option(label=regra.tipo_regra)
        elif self._visivel(self.tipo_regra_select) and self.tipo_regra_select.locator(
            "option", has_text=regra.tipo_regra
        ).count() > 0:
            self.tipo_regra_select.select_option(label=regra.tipo_regra)

        if regra.tipo_regra == "Não exibe prêmio":
            self.subs_balanceada_select.select_option(value=str(regra.subs_balanceada))
            if regra.subs_balanceada is True:
                self.mensagem_subs_balanceada_input.fill(
                    "Encaminhar para mesa de Subscrição Balanceada."
                )
            self.mensagem_hdi_digital_input.fill("Motivos técnicos.")

    @staticmethod
    def _para_datetime(valor: Any) -> datetime:
        if isinstance(valor, datetime):
            return valor
        texto = str(valor).strip()
        for formato in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d%m%Y"):
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                continue
        raise ValueError(f"Data inválida para o formulário: {valor!r}")

    def _preencher_data(self, locator: Locator, valor: Any) -> None:
        """Preenche data aceitando input type=date ou campo de texto com máscara."""
        data = self._para_datetime(valor)
        tipo_input = (locator.get_attribute("type") or "").lower()
        if tipo_input == "date":
            locator.fill(data.strftime("%Y-%m-%d"))
            return
        locator.click()
        locator.press("Control+A")
        locator.press("Delete")
        locator.type(data.strftime("%d/%m/%Y"), delay=50)

    def aplicar_acao(self, tipo_acao: str) -> None:
        tipo_acao = tipo_acao.strip().lower()
        if tipo_acao in {"inclusao", "inclusão"}:
            self.igual_btn.click()
        elif tipo_acao in {"exclusao", "exclusão"}:
            self.diferente_btn.click()
        else:
            raise ValueError(f"Ação inválida: {tipo_acao!r}")

    @staticmethod
    def _item_corresponde(item: dict, ids_dominio: set[int], textos: set[str]) -> bool:
        if ids_dominio:
            try:
                return int(item.get("IdDominio")) in ids_dominio
            except (TypeError, ValueError):
                return False
        # Sem IDs: aceita o item se algum campo for exatamente um dos textos.
        for valor in item.values():
            if valor is not None and str(valor).strip() in textos:
                return True
        return False

    @classmethod
    def _filtrar_grid(
        cls,
        data: Any,
        ids_dominio: set[int],
        textos: set[str],
    ) -> dict[str, Any] | None:
        """Filtra somente respostas finais, nas quais Grid é uma lista.

        Retorna None para respostas intermediárias, como Grid=None.
        """
        if not isinstance(data, dict):
            return None

        grid = data.get("Grid")
        if not isinstance(grid, list):
            return None

        filtrado = [
            item for item in grid
            if isinstance(item, dict) and cls._item_corresponde(item, ids_dominio, textos)
        ]

        resultado = dict(data)
        resultado["Grid"] = filtrado

        for contador in (
            "Total",
            "TotalSelecionado",
            "recordsTotal",
            "recordsFiltered",
            "total",
        ):
            if contador in resultado:
                resultado[contador] = len(filtrado)

        return resultado

    def _instalar_filtro_grid(
        self,
        variavel: Any,
        id_variavel: int,
    ) -> bool:
        """Instala o filtro para as variáveis em VARIAVEIS_GRID_PESADO."""
        id_variavel = int(id_variavel)
        if id_variavel not in self.VARIAVEIS_GRID_PESADO:
            return False

        ids_dominio = {int(i) for i in getattr(variavel, "ids_dominio", []) or []}
        textos = {str(v).strip() for v in variavel.valores if str(v).strip()}
        if not ids_dominio and not textos:
            raise ValueError(f"Variável {id_variavel} sem valores para filtrar o grid.")

        self._grid_filtro_concluido = False
        self._grid_filtro_erro = None
        self._grid_respostas_intermediarias = 0
        self._grid_quantidade_original = None
        self._grid_quantidade_filtrada = None

        def handler(route: Route) -> None:
            request = route.request

            if request.method != "POST":
                route.continue_()
                return

            try:
                payload = request.post_data_json
            except Exception:
                route.continue_()
                return

            if (
                not isinstance(payload, dict)
                or str(payload.get("IdVariavel")) != str(id_variavel)
            ):
                route.continue_()
                return

            try:
                inicio_fetch = perf_counter()

                if self.PARAMETROS_EXTRAS_REQUISICAO_GRID:
                    novo_payload = {**payload, **self.PARAMETROS_EXTRAS_REQUISICAO_GRID}
                    response = route.fetch(
                        post_data=json.dumps(novo_payload),
                        timeout=self.TIMEOUT_FETCH_GRID_MS,
                    )
                else:
                    response = route.fetch(timeout=self.TIMEOUT_FETCH_GRID_MS)

                logger.info(
                    "[GRID] route.fetch %.2fs",
                    perf_counter() - inicio_fetch,
                )
                if not response.ok:
                    route.fulfill(response=response)
                    return

                try:
                    data = response.json()
                except Exception:
                    route.fulfill(response=response)
                    return

                grid = data.get("Grid") if isinstance(data, dict) else None

                # A aplicação retorna primeiro Grid=None. Essa resposta precisa
                # chegar intacta ao frontend, e a rota deve continuar instalada.
                if grid is None:
                    self._grid_respostas_intermediarias += 1
                    logger.info(
                        "[filtro-grid] Resposta intermediária recebida: "
                        "Grid=None. Aguardando resposta final."
                    )
                    route.fulfill(response=response)
                    return

                # Formato inesperado: repassa sem quebrar o fluxo.
                if not isinstance(grid, list):
                    logger.info(
                        "[filtro-grid] Grid possui formato inesperado: "
                        f"{type(grid).__name__}. Repassando resposta original."
                    )
                    route.fulfill(response=response)
                    self._grid_filtro_concluido = True
                    return

                corpo = self._filtrar_grid(data, ids_dominio, textos)
                if corpo is None:
                    route.fulfill(response=response)
                    self._grid_filtro_concluido = True
                    return

                self._grid_quantidade_original = len(grid)
                self._grid_quantidade_filtrada = len(corpo["Grid"])

                if not corpo["Grid"]:
                    logger.info(
                        f"[filtro-grid] ATENÇÃO: nenhum item do grid bateu com "
                        f"ids={sorted(ids_dominio)} textos={sorted(textos)}."
                    )

                route.fulfill(response=response, json=corpo)
                self._grid_filtro_concluido = True

                logger.info(
                    "[GRID] variável=%s | recebidos=%s | enviados=%s",
                    id_variavel,
                    len(grid),
                    len(corpo["Grid"]),
                )

            except Exception as erro:
                self._grid_filtro_erro = erro
                logger.info(
                    "[filtro-grid] Erro no handler: "
                    f"{type(erro).__name__}: {erro}"
                )
                try:
                    route.continue_()
                except Exception:
                    pass

        self._grid_handler = handler
        self.page.route(self.GRID_API_PATTERN, handler)
        logger.info("[GRID] Rota instalada para variável %s", id_variavel)
        return True

    def _esperar_filtro_grid_concluir(self, timeout: int | None = None) -> None:
        """Aguarda a resposta final mantendo o loop de eventos ativo."""
        if timeout is None:
            timeout = self.TIMEOUT_ESPERA_GRID_MS
        decorrido = 0
        intervalo = 100

        while not self._grid_filtro_concluido:
            if self._grid_filtro_erro is not None:
                erro = self._grid_filtro_erro
                self._grid_filtro_erro = None
                raise RuntimeError("Erro durante o filtro da API do grid.") from erro

            if decorrido >= timeout:
                raise TimeoutError(
                    "A API não retornou uma lista em 'Grid' dentro de "
                    f"{timeout} ms. Respostas intermediárias: "
                    f"{self._grid_respostas_intermediarias}."
                )

            self.page.wait_for_timeout(intervalo)
            decorrido += intervalo


    def _remover_filtro_grid(self) -> None:
        handler = self._grid_handler
        if handler is None:
            return

        self._grid_handler = None
        self.page.unroute(self.GRID_API_PATTERN, handler)
        logger.info("[GRID] Rota removida")

    def preencher_arvore_de_decisao(
        self,
        acao: AcaoVariavel,
    ) -> None:
        variavel = acao.variavel
        id_grupo = int(variavel.grupo_variavel_id)
        id_variavel = int(variavel.variavel_id)
        id_tipo_lista = int(variavel.tipo_lista_id)
        tipo_variavel = str(variavel.tipo_variavel_id).strip().casefold()

        eh_categorica = tipo_variavel in {"categorica", "categórica"}
        eh_continua = tipo_variavel in {"continua", "contínua"}
        if not (eh_categorica or eh_continua):
            raise ValueError(
                f"Tipo de variável desconhecido: {variavel.tipo_variavel_id}"
            )

        deve_filtrar = (
            eh_categorica and id_variavel in self.VARIAVEIS_GRID_PESADO
        )
        rota_instalada = False

        try:
            with medir_tempo("Selecionar grupo"):
                self._selecionar_opcao(
                    self.grupo_variavel_select,
                    id_grupo,
                    aguardar_depois=lambda: self._aguardar_opcao(
                        self.variavel_select, id_variavel
                    ),
                )

            # A rota precisa estar ativa antes da ação que dispara a API do grid.
            if deve_filtrar:
                rota_instalada = self._instalar_filtro_grid(
                    variavel=variavel,
                    id_variavel=id_variavel,
                )

            with medir_tempo("Selecionar variável"):
                self._selecionar_opcao(
                    self.variavel_select,
                    id_variavel,
                    aguardar_depois=lambda: self._aguardar_opcao(
                        self.tipo_select, id_tipo_lista
                    ),
                )

            with medir_tempo("Selecionar tipo"):
                self._selecionar_opcao(
                    self.tipo_select,
                    id_tipo_lista,
                )

            if eh_categorica:
                self._preencher_variavel_categorica(
                    acao=acao,
                    rota_instalada=rota_instalada,
                )
                return

            self._preencher_variavel_continua(acao)
        finally:
            if rota_instalada:
                self._remover_filtro_grid()

    def _esperar_grid_pronto(self, timeout: int = 200_000) -> None:
        self.page.locator("#indicadores_lista").wait_for(state="attached", timeout=timeout)
        processando = self.page.locator("#indicadores_lista_processing")
        if processando.count() > 0:
            expect(processando).to_be_hidden(timeout=timeout)

    def _pesquisar_no_grid(self, texto: str) -> None:
        """Usa a busca do DataTables para trazer a linha para a página visível."""
        busca = self.page.locator('#indicadores_lista_filter input[type="search"]')
        if busca.count() == 0:
            return
        expect(busca).to_be_visible(timeout=20_000)
        busca.fill("")
        busca.fill(texto)
        self._esperar_grid_pronto()

    def _localizar_linha_dominio(
        self,
        *,
        texto: str,
        id_dominio: str | None = None,
        textos_aceitos: list[str] | None = None,
        timeout: int = 30_000,
    ) -> Locator:
        linhas = self.page.locator("#indicadores_lista tbody tr")

        if id_dominio:
            linha_por_id = self.page.locator(
                f'#indicadores_lista tbody tr[id="{id_dominio}"]'
            )
            try:
                expect(linha_por_id).to_have_count(1, timeout=timeout)
                expect(linha_por_id).to_be_visible(timeout=timeout)
            except AssertionError:
                linha_por_id = None
            if linha_por_id is not None:
                # Conferência: a linha do ID precisa mostrar o VALOR ou a descrição
                # (DOMINIO) desse mesmo domínio. O grid do IPOP costuma mostrar só a
                # descrição (ex.: Código Parceiro VALOR "10" aparece como "Ituran").
                aceitos = [t for t in dict.fromkeys([texto, *(textos_aceitos or [])]) if t]
                padrao = re.compile("|".join(re.escape(t) for t in aceitos), re.IGNORECASE)
                try:
                    expect(linha_por_id).to_contain_text(padrao, timeout=5_000)
                except AssertionError as erro:
                    raise RuntimeError(
                        f"A linha com IdDominio {id_dominio} não mostra nenhum dos textos "
                        f"esperados {aceitos!r} (mostra {linha_por_id.inner_text().strip()!r}). "
                        "Parando para não cadastrar valor errado."
                    ) from erro
                return linha_por_id

        # Fallback: célula com texto EXATO (evita '10' casar com '100').
        padrao = re.compile(rf"^\s*{re.escape(texto)}\s*$")
        linha_por_texto = linhas.filter(
            has=self.page.locator("td").filter(has_text=padrao)
        )
        expect(linha_por_texto.first).to_be_visible(timeout=timeout)
        quantidade = linha_por_texto.count()
        if quantidade > 1:
            logger.info(
                f"[grid] AVISO: {quantidade} linhas com o texto {texto!r}; "
                "selecionando a primeira."
            )
        return linha_por_texto.first

    def _preencher_variavel_categorica(
        self,
        *,
        acao: AcaoVariavel,
        rota_instalada: bool,
    ) -> None:
        variavel = acao.variavel
        valores = [str(v) for v in variavel.valores]
        ids = list(getattr(variavel, "ids_dominio", []) or [])
        if ids and len(ids) != len(valores):
            ids = []
        # Descrição (DOMINIO) de cada IdDominio, vinda da mesma linha do SQL.
        descricoes = {
            str(int(d["ID"])): str(d["DOMINIO"]).strip()
            for d in (getattr(variavel, "dominios", None) or [])
            if d.get("ID") is not None and d.get("DOMINIO") not in (None, "")
        }

        if rota_instalada:
            with medir_tempo("Filtro Grid"):
                self._esperar_filtro_grid_concluir()
        self._esperar_grid_pronto()

        for posicao, texto in enumerate(valores):
            id_dominio = ids[posicao] if ids else None
            with medir_tempo(f"Selecionar domínio {texto}"):
                if not rota_instalada:
                    # Grid completo pode ter paginação: pesquisar primeiro.
                    self._pesquisar_no_grid(texto)
                linha = self._localizar_linha_dominio(
                    texto=texto,
                    id_dominio=id_dominio,
                    textos_aceitos=[descricoes[id_dominio]] if id_dominio in descricoes else None,
                )
                linha.click(timeout=30_000)

        with medir_tempo("Aplicar ação"):
            self.aplicar_acao(acao.tipo_acao)

    def _preencher_variavel_continua(self, acao: AcaoVariavel) -> None:
        expect(self.valor_inicial_input).to_be_editable(timeout=20_000)
        expect(self.valor_final_input).to_be_editable(timeout=20_000)
        adicionar_range_btn = self.page.locator("#adicionar_range_button")

        for intervalo in acao.variavel.valores:
            try:
                minimo, maximo = intervalo
            except (TypeError, ValueError) as erro:
                raise ValueError(
                    f"Intervalo inválido: {intervalo!r}. Esperado: (mínimo, máximo)."
                ) from erro

            self.valor_inicial_input.fill(str(minimo))
            self.valor_final_input.fill(str(maximo))
            adicionar_range_btn.click(timeout=20_000)

        selecionar_todos = self.page.locator(
            'button[aria-controls="indicadores_range"]'
            '[title="Selecionar todas as linhas visíveis"]'
        )
        expect(selecionar_todos).to_be_enabled(timeout=20_000)
        selecionar_todos.click()
        self.aplicar_acao(acao.tipo_acao)

    def salvar_regra(self) -> None:
        expect(self.salvar_btn).to_be_enabled(timeout=30_000)
        self.salvar_btn.click()

    def fechar_pagina_de_cadastro(self) -> None:
        expect(self.fechar_regra_btn.first).to_be_visible(timeout=30_000)
        self.fechar_regra_btn.first.click()
